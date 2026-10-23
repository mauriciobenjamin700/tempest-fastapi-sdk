"""CSRF for HTML forms posted without JavaScript, and safe redirect targets.

:class:`~tempest_fastapi_sdk.CSRFMiddleware` compares a header with a
cookie, which a plain ``<form>`` cannot send: every POST from a page with
no script is refused with a JSON ``403``. The server-side auth pages need
the token in a form field instead.

:class:`FormCSRF` is the double-submit pattern, field-shaped: a random
token lives in an ``HttpOnly`` cookie and is repeated in a hidden
``csrf_token`` input; the submit compares the two in constant time. A
page on another site can make the browser *send* the cookie, but it can
neither read it nor learn the token to put in the field.

It covers the forms that run **before** a session exists — login,
signup, the resend form — which is exactly where a session-bound token
cannot help (login CSRF), and keeps working after login for logout.

The weakness of a plain double-submit is a cookie someone else can
**set**: a sibling subdomain (``evil.example.com`` for
``app.example.com``) can plant a cookie of its choosing for the parent
domain, together with a field of the same value, and pass the check.
Signing the token would not help — the attacker gets a validly signed
one by loading the form themselves. What does is the ``__Host-`` cookie
prefix, which a browser only accepts with ``Secure``, ``Path=/`` and no
``Domain``, so no other host can set it. :class:`FormCSRF` uses it
whenever the cookie is ``Secure``; over plain HTTP (local development)
the prefix is not allowed and the plain name is used.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

FORM_CSRF_COOKIE: Final[str] = "tempest_auth_csrf"
"""Cookie carrying the form token of the auth pages."""

FORM_CSRF_FIELD: Final[str] = "csrf_token"
"""Name of the hidden input every auth page form carries."""

FORM_CSRF_MAX_AGE_SECONDS: Final[int] = 60 * 60
"""How long a rendered form stays submittable.

An hour covers a form left open while the user looks up a password; a
later submit is refused and the form comes back with a fresh token.
"""

FORM_CSRF_TOKEN_BYTES: Final[int] = 32
"""Entropy of a token, as passed to :func:`secrets.token_urlsafe`."""

HOST_COOKIE_PREFIX: Final[str] = "__Host-"
"""Cookie-name prefix a browser accepts only with ``Secure``, ``Path=/``
and no ``Domain`` (RFC 6265bis, "Cookie Name Prefixes")."""


class FormCSRF:
    """Double-submit CSRF token for HTML forms.

    Attributes:
        cookie_name (str): Name of the cookie holding the token —
            prefixed with :data:`HOST_COOKIE_PREFIX` when ``secure`` and
            ``path`` is ``/``.
        secure (bool): Whether the cookie is flagged ``Secure``.
        samesite (Literal["lax", "strict", "none"]): ``SameSite`` policy.
        path (str): Cookie path.
        max_age_seconds (int): Cookie lifetime.
    """

    def __init__(
        self,
        *,
        cookie_name: str = FORM_CSRF_COOKIE,
        secure: bool = True,
        samesite: Literal["lax", "strict", "none"] = "lax",
        path: str = "/",
        max_age_seconds: int = FORM_CSRF_MAX_AGE_SECONDS,
    ) -> None:
        """Configure the cookie.

        Args:
            cookie_name (str): Name of the cookie holding the token.
            secure (bool): Flag the cookie ``Secure``. Leave ``True``
                behind HTTPS; a ``Secure`` cookie is not sent back over
                plain HTTP, which makes every submit fail.
            samesite (Literal["lax", "strict", "none"]): ``SameSite``.
            path (str): Cookie path. ``/``, the only path the ``__Host-``
                prefix allows.
            max_age_seconds (int): Cookie lifetime.
        """
        self.cookie_name: str = (
            f"{HOST_COOKIE_PREFIX}{cookie_name}"
            if secure and path == "/"
            else cookie_name
        )
        self.secure: bool = secure
        self.samesite: Literal["lax", "strict", "none"] = samesite
        self.path: str = path
        self.max_age_seconds: int = max_age_seconds

    def issue(self, request: Request) -> str:
        """Return the token to embed in the form being rendered.

        Reuses the request's token so two tabs do not invalidate each
        other; mints a new one when the request carries none.

        Args:
            request (Request): The inbound request.

        Returns:
            str: The token for the hidden field.
        """
        current = request.cookies.get(self.cookie_name)
        if current:
            return current
        return secrets.token_urlsafe(FORM_CSRF_TOKEN_BYTES)

    def attach(self, response: Response, token: str) -> None:
        """Set the cookie carrying ``token``, restarting its lifetime.

        Args:
            response (Response): The outbound response.
            token (str): The token embedded in the form.
        """
        response.set_cookie(
            self.cookie_name,
            token,
            max_age=self.max_age_seconds,
            httponly=True,
            secure=self.secure,
            samesite=self.samesite,
            path=self.path,
        )

    def verify(self, request: Request, submitted: str) -> bool:
        """Compare the submitted field with the cookie, in constant time.

        Args:
            request (Request): The inbound request.
            submitted (str): The ``csrf_token`` form field.

        Returns:
            bool: ``True`` only when both are present and equal.
        """
        expected = request.cookies.get(self.cookie_name)
        if not expected or not submitted:
            return False
        return secrets.compare_digest(expected, submitted)


def is_local_path(value: str) -> bool:
    """Whether ``value`` is a path on this site, safe to redirect to.

    Accepts ``/x``, ``/x?y=1`` and ``/``. Refuses anything a browser
    could resolve to another origin: an absolute URL, a scheme-relative
    ``//host``, the ``/\\host`` form some browsers normalize to it, and
    control characters that some parsers strip before resolving.

    Args:
        value (str): The candidate target, typically a ``next`` parameter.

    Returns:
        bool: ``True`` when redirecting to it stays on this site.
    """
    if not value.startswith("/") or value.startswith(("//", "/\\")):
        return False
    return all(ord(char) >= 0x20 and char != "\\" for char in value)


__all__: list[str] = [
    "FORM_CSRF_COOKIE",
    "FORM_CSRF_FIELD",
    "FORM_CSRF_MAX_AGE_SECONDS",
    "FORM_CSRF_TOKEN_BYTES",
    "HOST_COOKIE_PREFIX",
    "FormCSRF",
    "is_local_path",
]
