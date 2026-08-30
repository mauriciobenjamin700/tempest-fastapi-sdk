"""Authentication / authorization dependencies."""

from __future__ import annotations

import hmac
from collections.abc import Callable, Collection, Coroutine, Iterable
from typing import TYPE_CHECKING, Any

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from tempest_fastapi_sdk.exceptions.forbidden import ForbiddenException
from tempest_fastapi_sdk.exceptions.unauthorized import UnauthorizedException
from tempest_fastapi_sdk.utils.token_types import (
    ACCESS_TOKEN_TYPE,
    token_type_allowed,
)

if TYPE_CHECKING:
    from tempest_fastapi_sdk.utils.jwt import JWTUtils


def make_token_dependency(
    secret: str,
    *,
    header_name: str = "X-Token",
    error_message: str = "Invalid or missing token",
) -> Callable[..., Coroutine[Any, Any, None]]:
    """Build a FastAPI dependency that validates a shared-secret header.

    The returned coroutine compares the inbound header value with
    ``secret`` using :func:`hmac.compare_digest` (constant-time). An
    empty ``secret`` disables the check entirely — intentional for
    local development; production deployments should always provide
    a non-empty value.

    Args:
        secret (str): The shared secret to compare against. Empty
            string disables enforcement.
        header_name (str): The header to read. Defaults to
            ``"X-Token"``.
        error_message (str): Message attached to the raised
            :class:`UnauthorizedException`.

    Returns:
        Callable[..., Coroutine[Any, Any, None]]: An async FastAPI
        dependency that raises :class:`UnauthorizedException` on
        mismatch and returns ``None`` on success.
    """
    alias = header_name

    async def _require_token(token: str = Header(default="", alias=alias)) -> None:
        if not secret:
            return
        if not hmac.compare_digest(token, secret):
            raise UnauthorizedException(message=error_message)

    _require_token.__doc__ = (
        f"Validate the {alias} header against the configured shared secret."
    )
    return _require_token


async def require_x_token(
    secret: str,
    token: str,
    *,
    error_message: str = "Invalid or missing token",
) -> None:
    """Imperative variant of :func:`make_token_dependency`.

    Useful when validation happens outside the FastAPI dependency
    pipeline (e.g. from a websocket handler or background task).

    Args:
        secret (str): The shared secret.
        token (str): The token to verify.
        error_message (str): Message attached on failure.

    Raises:
        UnauthorizedException: When ``secret`` is non-empty and the
            tokens do not match in constant time.
    """
    if not secret:
        return
    if not hmac.compare_digest(token, secret):
        raise UnauthorizedException(message=error_message)


def make_bearer_token_dependency(
    tokens: JWTUtils,
    *,
    soft: bool = False,
    bearer_scheme: HTTPBearer | None = None,
    cookie_name: str | None = None,
    query_param: str | None = None,
    accepted_typ: Collection[str] = (ACCESS_TOKEN_TYPE,),
    strict: bool = False,
    legacy_claims: Collection[str] = (),
    error_message: str = "Authorization token is missing or invalid",
) -> Callable[..., Coroutine[Any, Any, dict[str, Any] | None]]:
    """Build a FastAPI dependency that decodes a JWT from header/cookie/query.

    Returns the decoded claims dict so the caller can wire its own
    ``get_current_user`` on top, combining the decoded subject with
    the project's session / repository conventions.

    Token lookup order, first hit wins:

    1. ``Authorization: Bearer <jwt>`` header.
    2. The ``cookie_name`` cookie, when set.
    3. The ``query_param`` query-string value, when set.

    The header/cookie seam is what lets the same dependency serve both
    bearer clients and the cookie delivery mode of
    :func:`tempest_fastapi_sdk.make_auth_router`.

    ``query_param`` exists for **cookieless** clients that cannot set an
    ``Authorization`` header — chiefly the browser ``EventSource`` used
    for SSE, whose constructor accepts neither headers nor a request
    body. Prefer a session cookie (``withCredentials``) whenever the
    client shares the API's origin; reach for the query string only
    when cross-origin or a raw ``EventSource`` leaves no other channel.

    .. warning::
        A token in the query string leaks into access logs, the browser
        history and any ``Referer`` header. Only enable ``query_param``
        with **short-lived** access tokens (never a refresh token),
        always over TLS, and scrub the value from your log format.

    Args:
        tokens (JWTUtils): The JWT helper used to verify the token.
        soft (bool): When ``True``, return ``None`` on missing or
            invalid tokens instead of raising. Useful for endpoints
            that work both authenticated and anonymous.
        bearer_scheme (HTTPBearer | None): Override the bearer scheme.
            Defaults to ``HTTPBearer(auto_error=False)``.
        cookie_name (str | None): When set, fall back to this cookie for
            the access token if the ``Authorization`` header is absent.
            ``None`` (default) skips the cookie lookup.
        query_param (str | None): When set, fall back to this
            query-string parameter (e.g. ``"access_token"``) if header
            and cookie are both absent. ``None`` (default) skips it.
            See the security warning above before enabling.
        accepted_typ (Collection[str]): Token types this dependency
            accepts, matched against the ``typ`` claim. Defaults to
            ``(ACCESS_TOKEN_TYPE,)`` — a refresh token or the
            MFA-pending token from step one of a two-step login is
            **rejected**, even though both are validly signed with the
            same secret. Widen it only for a route that deliberately
            takes another kind of token.
        strict (bool): How to treat a token that carries no
            recognizable type marker. ``False`` (default) accepts it —
            the compatibility window that keeps sessions minted before
            the ``typ`` claim existed from being logged out on upgrade.
            ``True`` refuses it. **A service whose legacy tokens
            declared their type under a claim of their own (``type``,
            ``token_type``) has neither ``typ`` nor an SDK fallback
            marker on any of them**, so under the default every one of
            those refresh tokens authorizes any route for the length of
            its TTL. Such a service wants ``strict=True`` plus
            ``legacy_claims``, and until v0.274.0 could only get there
            by not using this factory.
        legacy_claims (Collection[str]): Extra claim names to read the
            token type from when ``typ`` is absent, in order. Pair with
            ``strict=True``: the claims classify the old tokens, and
            strict refuses whatever stays unclassified. See
            :func:`~tempest_fastapi_sdk.token_type_allowed`.
        error_message (str): Message attached to the raised
            :class:`UnauthorizedException` when ``soft`` is ``False``.

    Returns:
        Callable[..., Coroutine[Any, Any, dict[str, Any] | None]]: An
        async FastAPI dependency yielding the decoded claims dict
        (or ``None`` when ``soft=True`` and the token is absent /
        invalid).
    """
    scheme: HTTPBearer = bearer_scheme or HTTPBearer(auto_error=False)

    async def _decode_bearer(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(scheme),
    ) -> dict[str, Any] | None:
        raw_token: str | None = (
            credentials.credentials if credentials is not None else None
        )
        if raw_token is None and cookie_name is not None:
            raw_token = request.cookies.get(cookie_name)
        if raw_token is None and query_param is not None:
            raw_token = request.query_params.get(query_param)
        if raw_token is None:
            if soft:
                return None
            raise UnauthorizedException(message=error_message)
        if soft:
            payload = tokens.decode_or_none(raw_token)
            if payload is None or not token_type_allowed(
                payload,
                accepted_typ,
                strict=strict,
                legacy_claims=legacy_claims,
            ):
                return None
            return payload
        payload = tokens.decode(raw_token)
        if not token_type_allowed(
            payload,
            accepted_typ,
            strict=strict,
            legacy_claims=legacy_claims,
        ):
            raise UnauthorizedException(message=error_message)
        return payload

    _decode_bearer.__doc__ = (
        "Decode the JWT (Authorization header, cookie or query param) "
        "and return its claims."
    )
    return _decode_bearer


def make_jwt_user_dependency(
    tokens: JWTUtils,
    user_loader: Callable[..., Coroutine[Any, Any, Any]],
    *,
    soft: bool = False,
    bearer_scheme: HTTPBearer | None = None,
    cookie_name: str | None = None,
    query_param: str | None = None,
    accepted_typ: Collection[str] = (ACCESS_TOKEN_TYPE,),
    strict: bool = False,
    legacy_claims: Collection[str] = (),
    subject_claim: str = "sub",
    error_message: str = "Authorization token is missing or invalid",
    session_dependency: Callable[..., Any] | None = None,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Build a FastAPI dependency that returns the authenticated user.

    The dependency:

    1. Reads the JWT from ``Authorization: Bearer`` (or, when
       ``cookie_name`` is set, falls back to that cookie).
    2. Decodes / verifies the JWT with ``tokens``.
    3. Pulls the user identifier from the configured ``subject_claim``.
    4. Awaits ``user_loader(<id>)`` and returns the user it yields.

    ``user_loader`` is the single seam where the service maps
    ``payload[subject_claim]`` to an actual user. **Returning ``None``
    from it refuses the request**: with ``soft=False`` that is an
    :class:`UnauthorizedException`, the same answer an absent subject gets.
    In soft mode it stays ``None``, which is what the flag is for.

    **Session sharing.** When ``session_dependency`` is given, the
    returned dependency declares it as a sub-dependency and calls the
    two-argument ``user_loader(subject, session)`` with the
    **request-scoped** session. Because FastAPI caches a sub-dependency
    by its callable within a single request, the authenticated user is
    loaded on the *same* session the request's repositories use — so the
    instance stays attached and can be mutated / refreshed without the
    ``InvalidRequestError: Instance is not persistent within this
    Session`` that a per-loader private session would cause. Pass the
    **exact** callable your repositories depend on (e.g.
    ``db.session_dependency``); a different wrapper would resolve to a
    second, distinct session and defeat the sharing.

    When ``session_dependency`` is ``None`` the loader is called with a
    single argument (``user_loader(subject)``) and owns its own session
    lifecycle — useful outside a request scope, but it yields a
    **detached** instance.

    Args:
        tokens (JWTUtils): The JWT helper used to verify the token.
        user_loader (Callable[..., Coroutine[Any, Any, Any]]): Async
            callable that receives the subject (typically the user id
            as a string) and returns the loaded user. When
            ``session_dependency`` is set it is called as
            ``user_loader(subject, session)``. Returning ``None``
            refuses the request when ``soft`` is ``False``; raising
            :class:`UnauthorizedException` or
            :class:`NotFoundException` from inside the loader still works
            and lets you choose the message.
        soft (bool): When ``True``, return ``None`` instead of
            raising on a missing or invalid token, and when the loader
            declines the subject.
        bearer_scheme (HTTPBearer | None): Override the bearer scheme.
        cookie_name (str | None): When set, fall back to this cookie for
            the access token when the ``Authorization`` header is absent.
        query_param (str | None): When set, fall back to this
            query-string parameter for the access token (cookieless
            clients such as ``EventSource``). See the security warning
            on :func:`make_bearer_token_dependency` before enabling.
        accepted_typ (Collection[str]): Token types accepted, matched
            against the ``typ`` claim. Defaults to access-only, so the
            refresh token and the MFA-pending token cannot stand in for
            an access token. See
            :func:`make_bearer_token_dependency`.
        strict (bool): How to treat a token that carries no
            recognizable type marker. ``False`` (default) accepts it —
            the compatibility window that keeps sessions minted before
            the ``typ`` claim existed from being logged out on upgrade.
            ``True`` refuses it. **A service whose legacy tokens
            declared their type under a claim of their own (``type``,
            ``token_type``) has neither ``typ`` nor an SDK fallback
            marker on any of them**, so under the default every one of
            those refresh tokens authorizes any route for the length of
            its TTL. Such a service wants ``strict=True`` plus
            ``legacy_claims``, and until v0.274.0 could only get there
            by not using this factory.
        legacy_claims (Collection[str]): Extra claim names to read the
            token type from when ``typ`` is absent, in order. Pair with
            ``strict=True``: the claims classify the old tokens, and
            strict refuses whatever stays unclassified. See
            :func:`~tempest_fastapi_sdk.token_type_allowed`.
        subject_claim (str): Which JWT claim carries the user id.
            Defaults to ``"sub"``.
        error_message (str): Message attached to the raised
            :class:`UnauthorizedException` when ``soft`` is ``False``.
        session_dependency (Callable[..., Any] | None): The
            request-scoped session provider to share with repositories.
            When set, ``user_loader`` is called with ``(subject,
            session)``.

    Returns:
        Callable[..., Coroutine[Any, Any, Any]]: An async FastAPI
        dependency yielding the user (or ``None`` in soft mode).
    """
    decode_bearer = make_bearer_token_dependency(
        tokens,
        soft=soft,
        bearer_scheme=bearer_scheme,
        cookie_name=cookie_name,
        query_param=query_param,
        accepted_typ=accepted_typ,
        strict=strict,
        legacy_claims=legacy_claims,
        error_message=error_message,
    )

    def _resolved(user: Any) -> Any:
        """Apply the ``soft`` rule to whatever the loader returned.

        Args:
            user (Any): The loader's return value.

        Returns:
            Any: The user, or ``None`` in soft mode.

        Raises:
            UnauthorizedException: When the loader declined the subject
                and ``soft`` is ``False``.

        The loader is documented as the seam where a service refuses a
        subject — deactivated account, id that no longer resolves, malformed
        subject. Returning ``None`` there used to reach the handler as
        ``None``, so the route answered 200 with a user the loader had
        rejected, and deactivating an account had no effect until the access
        token expired. A declined subject is the same outcome as an absent
        one, so it gets the same answer.
        """
        if user is None and not soft:
            raise UnauthorizedException(message=error_message)
        return user

    if session_dependency is None:

        async def _current_user_owns_session(
            payload: dict[str, Any] | None = Depends(decode_bearer),
        ) -> Any:
            if payload is None:
                return None
            subject = payload.get(subject_claim)
            if subject is None:
                if soft:
                    return None
                raise UnauthorizedException(message=error_message)
            return _resolved(await user_loader(subject))

        dependency = _current_user_owns_session

    else:

        async def _current_user_shared_session(
            payload: dict[str, Any] | None = Depends(decode_bearer),
            session: Any = Depends(session_dependency),
        ) -> Any:
            if payload is None:
                return None
            subject = payload.get(subject_claim)
            if subject is None:
                if soft:
                    return None
                raise UnauthorizedException(message=error_message)
            return _resolved(await user_loader(subject, session))

        dependency = _current_user_shared_session

    dependency.__doc__ = "Decode the bearer JWT and resolve the authenticated user."
    return dependency


def make_role_dependency(
    tokens: JWTUtils,
    required_roles: Iterable[str],
    *,
    roles_claim: str = "roles",
    require_all: bool = False,
    bearer_scheme: HTTPBearer | None = None,
    unauthorized_message: str = "Authorization token is missing or invalid",
    forbidden_message: str = "Insufficient role for this operation",
) -> Callable[..., Coroutine[Any, Any, None]]:
    """Build a FastAPI dependency that gates a route by JWT role claims.

    The dependency decodes the bearer JWT, reads the configured roles
    claim (a string or list of strings) and compares it against
    ``required_roles``. ``require_all=False`` (default) authorizes the
    request when any required role is present; ``require_all=True``
    requires every listed role.

    Args:
        tokens (JWTUtils): The JWT helper used to verify the token.
        required_roles (Iterable[str]): Roles the route accepts.
        roles_claim (str): Which JWT claim carries the roles.
            Defaults to ``"roles"``.
        require_all (bool): When ``True``, every role in
            ``required_roles`` must be present.
        bearer_scheme (HTTPBearer | None): Override the bearer scheme.
        unauthorized_message (str): Message attached to the raised
            :class:`UnauthorizedException` when the token is missing
            or invalid.
        forbidden_message (str): Message attached to the raised
            :class:`ForbiddenException` when the token is valid but
            the role requirement is not satisfied.

    Returns:
        Callable[..., Coroutine[Any, Any, None]]: An async FastAPI
        dependency raising :class:`UnauthorizedException` /
        :class:`ForbiddenException` as appropriate, ``None`` on
        success.
    """
    required: set[str] = set(required_roles)
    decode_bearer = make_bearer_token_dependency(
        tokens,
        bearer_scheme=bearer_scheme,
        error_message=unauthorized_message,
    )

    async def _require_roles(
        payload: dict[str, Any] | None = Depends(decode_bearer),
    ) -> None:
        if payload is None:
            raise UnauthorizedException(message=unauthorized_message)
        raw = payload.get(roles_claim, [])
        if isinstance(raw, str):
            held: set[str] = {raw}
        elif isinstance(raw, Iterable):
            held = {str(r) for r in raw}
        else:
            held = set()

        authorized = required.issubset(held) if require_all else bool(required & held)

        if not authorized:
            raise ForbiddenException(message=forbidden_message)

    _require_roles.__doc__ = (
        f"Require {'all' if require_all else 'any'} of: {sorted(required)}."
    )
    return _require_roles


def make_permission_dependency(
    tokens: JWTUtils,
    required_permissions: Iterable[str],
    *,
    permissions_claim: str = "permissions",
    require_all: bool = True,
    bearer_scheme: HTTPBearer | None = None,
    unauthorized_message: str = "Authorization token is missing or invalid",
    forbidden_message: str = "Insufficient permissions for this operation",
) -> Callable[..., Coroutine[Any, Any, None]]:
    """Build a FastAPI dependency that gates a route by permission claims.

    Same shape as :func:`make_role_dependency` but defaults to
    ``require_all=True`` since permissions are usually fine-grained
    capabilities (``orders:write``, ``users:delete``) where every
    listed permission must be granted.

    Args:
        tokens (JWTUtils): The JWT helper used to verify the token.
        required_permissions (Iterable[str]): Permissions the route
            requires.
        permissions_claim (str): JWT claim carrying the permissions.
            Defaults to ``"permissions"``.
        require_all (bool): When ``True`` (default), every listed
            permission must be present.
        bearer_scheme (HTTPBearer | None): Override the bearer scheme.
        unauthorized_message (str): Message attached to the
            :class:`UnauthorizedException`.
        forbidden_message (str): Message attached to the
            :class:`ForbiddenException`.

    Returns:
        Callable[..., Coroutine[Any, Any, None]]: An async FastAPI
        dependency.
    """
    return make_role_dependency(
        tokens,
        required_permissions,
        roles_claim=permissions_claim,
        require_all=require_all,
        bearer_scheme=bearer_scheme,
        unauthorized_message=unauthorized_message,
        forbidden_message=forbidden_message,
    )


__all__: list[str] = [
    "make_bearer_token_dependency",
    "make_jwt_user_dependency",
    "make_permission_dependency",
    "make_role_dependency",
    "make_token_dependency",
    "require_x_token",
]
