"""Token-delivery configuration for the bundled auth router.

``make_auth_router`` can hand the JWT pair back to the client in three
ways, selected by ``AuthSettings.AUTH_TOKEN_DELIVERY``:

* ``"bearer"`` — tokens in the JSON body only (the historical default);
  the client sends them back as ``Authorization: Bearer <token>``.
* ``"cookie"`` — tokens set as ``HttpOnly`` cookies; the body omits the
  token values. Safer against XSS since JavaScript can never read them.
* ``"both"`` — the bearer endpoints stay at ``/auth/*`` and a parallel
  set of cookie endpoints is mounted at ``/auth/cookie/*``.

This module holds the small, framework-level pieces shared by every
mode: the :data:`TokenDelivery` type, the :class:`AuthCookieConfig`
value object, and the two helpers that stamp / clear the cookie pair on
a response. The router owns the endpoint wiring; the auth dependency
owns reading the access token back out of the cookie.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from starlette.responses import Response

from tempest_fastapi_sdk.api.cookies import SameSite, clear_cookie, set_cookie

TokenDelivery = Literal["bearer", "cookie", "both"]
"""How the auth router returns the JWT pair. See the module docstring."""


@dataclass(frozen=True)
class AuthCookieConfig:
    """Security attributes for the auth cookie pair.

    The access cookie is scoped to ``/`` so it rides along with every
    request; the refresh cookie is scoped to ``refresh_path`` (the
    refresh endpoint) so the long-lived token is never sent on ordinary
    calls, shrinking its exposure.

    Args:
        access_name (str): Cookie name for the short-lived access token.
        refresh_name (str): Cookie name for the long-lived refresh
            token.
        access_max_age (int): Access-cookie lifetime in seconds. Mirror
            ``JWT_ACCESS_TTL_SECONDS`` so the browser drops it when the
            token expires.
        refresh_max_age (int): Refresh-cookie lifetime in seconds.
            Mirror ``JWT_REFRESH_TTL_SECONDS``.
        refresh_path (str): Path the refresh cookie is scoped to.
            Defaults to ``"/"``; the router narrows it to the refresh
            endpoint.
        secure (bool): Emit the ``Secure`` flag. Keep ``True`` in
            production; set ``False`` only for a plain-HTTP backend.
        samesite (SameSite): ``"lax"`` (default), ``"strict"`` or
            ``"none"``. Cross-site SPAs need ``"none"`` + ``secure=True``.
        domain (str | None): Cookie ``Domain``. ``None`` binds to the
            serving host.
        http_only (bool): Block JavaScript access. Defaults to ``True``
            and should stay so — it is the whole point of cookie mode.
    """

    access_name: str = "access_token"
    refresh_name: str = "refresh_token"
    access_max_age: int = 3600
    refresh_max_age: int = 1209600
    refresh_path: str = "/"
    secure: bool = True
    samesite: SameSite = "lax"
    domain: str | None = None
    http_only: bool = True


def apply_auth_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str | None,
    config: AuthCookieConfig,
) -> None:
    """Stamp the access (and optional refresh) cookie on ``response``.

    Args:
        response (Response): The outgoing response to mutate.
        access_token (str): The freshly issued access token.
        refresh_token (str | None): The freshly issued refresh token, or
            ``None`` to leave the refresh cookie untouched (e.g. an
            access-only rotation).
        config (AuthCookieConfig): The cookie security attributes.
    """
    set_cookie(
        response,
        config.access_name,
        access_token,
        max_age=config.access_max_age,
        path="/",
        domain=config.domain,
        http_only=config.http_only,
        secure=config.secure,
        samesite=config.samesite,
    )
    if refresh_token is not None:
        set_cookie(
            response,
            config.refresh_name,
            refresh_token,
            max_age=config.refresh_max_age,
            path=config.refresh_path,
            domain=config.domain,
            http_only=config.http_only,
            secure=config.secure,
            samesite=config.samesite,
        )


def clear_auth_cookies(
    response: Response,
    *,
    config: AuthCookieConfig,
) -> None:
    """Delete both auth cookies on ``response`` (logout).

    The delete attributes mirror :func:`apply_auth_cookies` so the
    browser actually drops the cookies — a ``delete_cookie`` whose
    ``path`` / ``domain`` / flags don't match the original is silently
    ignored.

    Args:
        response (Response): The outgoing response to mutate.
        config (AuthCookieConfig): The cookie security attributes.
    """
    clear_cookie(
        response,
        config.access_name,
        path="/",
        domain=config.domain,
        http_only=config.http_only,
        secure=config.secure,
        samesite=config.samesite,
    )
    clear_cookie(
        response,
        config.refresh_name,
        path=config.refresh_path,
        domain=config.domain,
        http_only=config.http_only,
        secure=config.secure,
        samesite=config.samesite,
    )


__all__: list[str] = [
    "AuthCookieConfig",
    "TokenDelivery",
    "apply_auth_cookies",
    "clear_auth_cookies",
]
