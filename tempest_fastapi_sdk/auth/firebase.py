"""Firebase ID token verification for services whose clients sign in with Firebase.

A mobile or web client authenticates against Firebase, receives an **ID
token** and sends it to the API. The service never issues that token — it
only has to prove the token is genuine before resolving the caller. This
is the same *resource server* shape as
:class:`tempest_fastapi_sdk.auth.introspection.IntrospectionAuth`, except
that verification is local (signature + claims against Google's rotating
public certificates) instead of a call to a ``userinfo`` endpoint.

The module wraps three things every service otherwise re-implements:

* **Idempotent app initialization.** ``firebase_admin.initialize_app()``
  raises ``ValueError`` when called twice, so services grow a
  ``get_app()`` / ``except ValueError`` dance around it.
  :class:`FirebaseAuth` owns that dance; constructing two instances with
  the same ``app_name`` reuses one underlying app.
* **A typed identity.** :class:`FirebaseIdentity` is what handlers see —
  never the raw ``dict[str, Any]`` of claims.
* **Errors in the SDK's hierarchy.** Every failure mode becomes an
  :class:`~tempest_fastapi_sdk.exceptions.UnauthorizedException` (or
  :class:`~tempest_fastapi_sdk.exceptions.ForbiddenException`) subclass
  with its own ``code``, so ``register_exception_handlers`` answers 401 /
  403 with a machine-readable identifier instead of leaking a
  ``firebase_admin`` exception as a 500.

Verification needs the optional ``[firebase]`` extra. It is imported
**lazily, at construction time**, so importing this module — or the whole
SDK — works without the extra installed::

    from tempest_fastapi_sdk.auth import FirebaseAuth, FirebaseIdentity

    auth = FirebaseAuth(credentials_path="credentials.json")

    @router.get("/me")
    async def me(
        identity: FirebaseIdentity = Depends(auth.get_identity),
    ) -> dict[str, str]:
        return {"uid": identity.uid, "email": identity.email or ""}
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Generic, TypeVar

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from tempest_fastapi_sdk.exceptions import (
    ForbiddenException,
    UnauthorizedException,
)

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)

DEFAULT_FIREBASE_APP_NAME: str = "[DEFAULT]"
"""Name ``firebase_admin`` gives the app created without an explicit one.

Ported from ``firebase_admin._DEFAULT_APP_NAME`` (measured on
``firebase-admin`` 7.5.0) so callers can name the default app explicitly
without importing a private attribute. ``tests/auth/test_firebase.py``
pins it against the installed package, so an upstream rename fails a test
instead of silently creating a second app.
"""

UserT = TypeVar("UserT")


class FirebaseCredentialError(RuntimeError):
    """Raised when the Firebase app cannot be initialized.

    This is a **configuration** failure (missing service-account file,
    malformed JSON, no application-default credential in the
    environment), not a request failure — it happens at construction
    time, never while handling a request, so it is not an
    :class:`~tempest_fastapi_sdk.exceptions.AppException`.
    """


class FirebaseTokenMissingError(UnauthorizedException):
    """Raised when a request carries no ``Authorization: Bearer`` header."""

    message: str = "Authentication required"
    code: str = "FIREBASE_TOKEN_MISSING"


class FirebaseTokenInvalidError(UnauthorizedException):
    """Raised when the ID token is malformed or fails verification."""

    message: str = "Invalid Firebase ID token"
    code: str = "FIREBASE_TOKEN_INVALID"


class FirebaseTokenExpiredError(UnauthorizedException):
    """Raised when the ID token's ``exp`` claim is in the past."""

    message: str = "Firebase ID token expired"
    code: str = "FIREBASE_TOKEN_EXPIRED"


class FirebaseTokenRevokedError(UnauthorizedException):
    """Raised when the ID token was revoked upstream.

    Only reachable with ``check_revoked=True``, which costs one extra
    call to the Firebase backend per verification.
    """

    message: str = "Firebase ID token revoked"
    code: str = "FIREBASE_TOKEN_REVOKED"


class FirebaseUserDisabledError(ForbiddenException):
    """Raised when the token is valid but the Firebase user is disabled.

    The caller proved who they are, so this is a 403 and not a 401. Like
    :class:`FirebaseTokenRevokedError`, it is only reachable with
    ``check_revoked=True``.
    """

    message: str = "Firebase user is disabled"
    code: str = "FIREBASE_USER_DISABLED"


class FirebaseUnavailableError(UnauthorizedException):
    """Raised when Google's public certificates cannot be fetched.

    Verification is local, but it needs the signing certificates, which
    ``firebase_admin`` fetches and caches. When that fetch fails the
    token can be neither accepted nor rejected. It answers 401 for the
    same reason ``IntrospectionAuth`` does when its upstream is
    unreachable: the request could not be authenticated.
    """

    message: str = "Could not reach the Firebase certificate endpoint"
    code: str = "FIREBASE_UNAVAILABLE"


def _require_firebase_admin() -> tuple[Any, Any, Any]:
    """Import ``firebase_admin`` lazily.

    Keeping the import inside a function is what lets
    ``import tempest_fastapi_sdk`` (and
    ``from tempest_fastapi_sdk.auth import FirebaseAuth``) work without
    the optional extra — the failure surfaces only when a
    :class:`FirebaseAuth` is actually constructed.

    Returns:
        tuple[Any, Any, Any]: The ``firebase_admin`` module, its
        ``credentials`` submodule and its ``auth`` submodule, in that
        order.

    Raises:
        ImportError: When the optional ``[firebase]`` extra is missing.
    """
    try:
        import firebase_admin
        from firebase_admin import auth as firebase_auth
        from firebase_admin import credentials as firebase_credentials
    except ImportError as exc:
        raise ImportError(
            "Firebase support requires the optional [firebase] extra. "
            "Install with: pip install tempest-fastapi-sdk[firebase]",
        ) from exc
    return firebase_admin, firebase_credentials, firebase_auth


@dataclass(frozen=True)
class FirebaseIdentity:
    """The verified caller, as the application sees it.

    Attributes:
        uid (str): The Firebase user id (the token's ``sub``).
        email (str | None): The email on the token, when the sign-in
            provider supplied one.
        email_verified (bool): Whether Firebase considers that email
            verified. ``False`` when the claim is absent.
        phone_number (str | None): The phone number on the token, for
            phone sign-in.
        provider (str): The ``firebase.sign_in_provider`` claim —
            ``"password"``, ``"google.com"``, ``"apple.com"``,
            ``"phone"``, ``"anonymous"``, … Empty when absent.
        claims (Mapping[str, Any]): Every claim the token carried,
            including custom claims set with ``set_custom_user_claims``.
            Read this for anything the fields above do not model.
    """

    uid: str
    email: str | None = None
    email_verified: bool = False
    phone_number: str | None = None
    provider: str = ""
    claims: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_claims(cls, claims: Mapping[str, Any]) -> FirebaseIdentity:
        """Build an identity from the claims ``verify_id_token`` returned.

        ``firebase_admin`` copies ``sub`` into a ``uid`` key on the
        verified claims, so ``uid`` is read first and ``sub`` is the
        fallback — the mapping works with either shape, including hand
        built claims in tests.

        Args:
            claims (Mapping[str, Any]): The verified token claims.

        Returns:
            FirebaseIdentity: The typed identity.

        Raises:
            FirebaseTokenInvalidError: When the claims carry neither
                ``uid`` nor ``sub``, so there is no subject to
                authenticate.
        """
        uid = str(claims.get("uid") or claims.get("sub") or "")
        if not uid:
            raise FirebaseTokenInvalidError(
                message="Firebase ID token is missing a subject claim"
            )
        firebase_claim = claims.get("firebase")
        provider = ""
        if isinstance(firebase_claim, Mapping):
            provider = str(firebase_claim.get("sign_in_provider") or "")
        email = claims.get("email")
        phone_number = claims.get("phone_number")
        return cls(
            uid=uid,
            email=str(email) if email else None,
            email_verified=bool(claims.get("email_verified", False)),
            phone_number=str(phone_number) if phone_number else None,
            provider=provider,
            claims=dict(claims),
        )


class FirebaseAuth:
    """Verify Firebase ID tokens and expose them as FastAPI dependencies.

    Construction initializes (or reuses) a ``firebase_admin`` app. The
    reuse is the point: ``initialize_app()`` raises ``ValueError`` when
    an app of that name already exists, so every service that wires
    Firebase in more than one module ends up re-implementing the same
    ``get_app()`` / ``except ValueError`` guard. Building two
    :class:`FirebaseAuth` instances with the same ``app_name`` is
    supported and hits the same underlying app.

    Credential precedence, first hit wins:

    1. ``credentials_json`` — the service-account JSON inline, for
       deployments that inject it as an environment variable and mount
       no file.
    2. ``credentials_path`` — a service-account file on disk.
    3. The environment's application-default credential
       (``GOOGLE_APPLICATION_CREDENTIALS``, or the metadata server on
       Google infrastructure).

    Both :meth:`get_identity` and :meth:`get_uid` are bound methods
    usable directly as FastAPI dependencies, and
    :meth:`get_optional_identity` is the soft variant that yields
    ``None`` instead of raising.

    Attributes:
        app_name (str): Name of the underlying ``firebase_admin`` app.
        check_revoked (bool): Whether each verification also asks the
            Firebase backend whether the token was revoked and whether
            the user is disabled. Costs one network round-trip per
            request.
        clock_skew_seconds (int): Tolerance, in seconds, applied to the
            token's time claims.
    """

    def __init__(
        self,
        *,
        credentials_path: str | Path | None = None,
        credentials_json: str | None = None,
        project_id: str | None = None,
        app_name: str = DEFAULT_FIREBASE_APP_NAME,
        check_revoked: bool = False,
        clock_skew_seconds: int = 0,
    ) -> None:
        """Initialize the authenticator, creating or reusing a Firebase app.

        Args:
            credentials_path (str | Path | None): Path to a
                service-account JSON file. Ignored when
                ``credentials_json`` is given.
            credentials_json (str | None): The service-account JSON
                itself, as a string. Takes precedence over
                ``credentials_path``.
            project_id (str | None): Firebase project id. Optional when
                the credential already carries it (a service-account
                file does); required with an application-default
                credential that does not.
            app_name (str): Name of the ``firebase_admin`` app to create
                or reuse. Defaults to
                :data:`DEFAULT_FIREBASE_APP_NAME`. Pass a distinct name
                to talk to a second Firebase project from the same
                process.
            check_revoked (bool): When ``True``, every verification also
                checks revocation and whether the user is disabled,
                which reaches the Firebase backend. Defaults to
                ``False`` (signature + claims only, no network per
                request once the certificates are cached).
            clock_skew_seconds (int): Seconds of tolerance for the
                token's ``iat`` / ``exp`` claims. Defaults to ``0``.

        Raises:
            ImportError: When the optional ``[firebase]`` extra is not
                installed.
            FirebaseCredentialError: When the credential cannot be built
                or the app cannot be initialized.
        """
        firebase_admin, firebase_credentials, firebase_auth = _require_firebase_admin()
        self.app_name: str = app_name
        self.check_revoked: bool = check_revoked
        self.clock_skew_seconds: int = clock_skew_seconds
        self._credentials_path: str | Path | None = credentials_path
        self._credentials_json: str | None = credentials_json
        self._project_id: str | None = project_id
        self._auth: Any = firebase_auth
        self._app: Any = self._get_or_create_app(firebase_admin, firebase_credentials)

    def _build_credential(self, firebase_credentials: Any) -> Any:
        """Build the ``firebase_admin`` credential from the configuration.

        Args:
            firebase_credentials (Any): The ``firebase_admin.credentials``
                module.

        Returns:
            Any: A ``Certificate`` credential when a service account was
            configured, otherwise ``ApplicationDefault``.

        Raises:
            FirebaseCredentialError: When the inline JSON does not parse
                or the file is missing / malformed.
        """
        if self._credentials_json:
            try:
                payload: dict[str, Any] = json.loads(self._credentials_json)
            except json.JSONDecodeError as error:
                raise FirebaseCredentialError(
                    "FIREBASE_CREDENTIALS_JSON is not valid JSON"
                ) from error
            try:
                return firebase_credentials.Certificate(payload)
            except (ValueError, OSError) as error:
                raise FirebaseCredentialError(
                    "FIREBASE_CREDENTIALS_JSON is not a valid service account"
                ) from error
        if self._credentials_path:
            try:
                return firebase_credentials.Certificate(str(self._credentials_path))
            except (ValueError, OSError) as error:
                raise FirebaseCredentialError(
                    f"Could not read the service account at {self._credentials_path!s}"
                ) from error
        try:
            return firebase_credentials.ApplicationDefault()
        except (ValueError, OSError) as error:
            raise FirebaseCredentialError(
                "No Firebase credential configured: pass credentials_path or "
                "credentials_json, or provide an application-default credential"
            ) from error

    def _get_or_create_app(self, firebase_admin: Any, firebase_credentials: Any) -> Any:
        """Return the named Firebase app, initializing it once.

        ``firebase_admin.get_app()`` raises ``ValueError`` when the app
        does not exist yet and ``initialize_app()`` raises ``ValueError``
        when it already does — so both directions are handled, which
        also makes two instances built concurrently converge on one app
        instead of one of them dying.

        Args:
            firebase_admin (Any): The ``firebase_admin`` module.
            firebase_credentials (Any): The ``firebase_admin.credentials``
                module.

        Returns:
            Any: The ``firebase_admin.App`` this instance verifies with.

        Raises:
            FirebaseCredentialError: When the app cannot be initialized.
        """
        try:
            return firebase_admin.get_app(self.app_name)
        except ValueError:
            pass
        credential = self._build_credential(firebase_credentials)
        options: dict[str, Any] | None = (
            {"projectId": self._project_id} if self._project_id else None
        )
        try:
            return firebase_admin.initialize_app(
                credential, options, name=self.app_name
            )
        except ValueError:
            try:
                return firebase_admin.get_app(self.app_name)
            except ValueError as error:
                raise FirebaseCredentialError(
                    f"Could not initialize the Firebase app {self.app_name!r}"
                ) from error

    async def verify(self, token: str) -> FirebaseIdentity:
        """Verify a raw ID token and return the identity it carries.

        ``firebase_admin.auth.verify_id_token`` is synchronous and may
        fetch Google's signing certificates, so it runs in a worker
        thread (:func:`asyncio.to_thread`), matching the SDK's
        async-first convention.

        The ``except`` clauses are ordered most-specific first because
        ``ExpiredIdTokenError`` and ``RevokedIdTokenError`` are both
        subclasses of ``InvalidIdTokenError`` (measured on
        ``firebase-admin`` 7.5.0). Catching the parent first would
        collapse all three into ``FIREBASE_TOKEN_INVALID``.

        Args:
            token (str): The raw ID token, without the ``Bearer`` prefix.

        Returns:
            FirebaseIdentity: The verified identity.

        Raises:
            FirebaseTokenRevokedError: When the token was revoked
                (``check_revoked=True`` only).
            FirebaseTokenExpiredError: When the token has expired.
            FirebaseUserDisabledError: When the Firebase user is disabled
                (``check_revoked=True`` only).
            FirebaseUnavailableError: When the signing certificates could
                not be fetched.
            FirebaseTokenInvalidError: When the token is malformed, has a
                bad signature, targets another project, or carries no
                subject claim.
        """
        try:
            claims: Mapping[str, Any] = await asyncio.to_thread(
                self._auth.verify_id_token,
                token,
                app=self._app,
                check_revoked=self.check_revoked,
                clock_skew_seconds=self.clock_skew_seconds,
            )
        except self._auth.RevokedIdTokenError as error:
            raise FirebaseTokenRevokedError() from error
        except self._auth.ExpiredIdTokenError as error:
            raise FirebaseTokenExpiredError() from error
        except self._auth.UserDisabledError as error:
            raise FirebaseUserDisabledError() from error
        except self._auth.CertificateFetchError as error:
            raise FirebaseUnavailableError() from error
        except (self._auth.InvalidIdTokenError, ValueError) as error:
            raise FirebaseTokenInvalidError() from error
        return FirebaseIdentity.from_claims(claims)

    async def get_identity(
        self,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
        ] = None,
    ) -> FirebaseIdentity:
        """FastAPI dependency returning the verified caller.

        Args:
            credentials (HTTPAuthorizationCredentials | None): The bearer
                credentials extracted by the ``HTTPBearer`` scheme, or
                ``None`` when no ``Authorization`` header was sent.

        Returns:
            FirebaseIdentity: The verified identity.

        Raises:
            FirebaseTokenMissingError: When no credentials were supplied.
            UnauthorizedException: Any of the verification failures
                documented on :meth:`verify`.
            FirebaseUserDisabledError: When the user is disabled (403).
        """
        if credentials is None:
            raise FirebaseTokenMissingError()
        return await self.verify(credentials.credentials)

    async def get_optional_identity(
        self,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
        ] = None,
    ) -> FirebaseIdentity | None:
        """FastAPI dependency returning the caller, or ``None``.

        The soft variant, for endpoints that serve both authenticated and
        anonymous callers. A **missing** header yields ``None``; so does
        a token that fails verification, which is logged at ``DEBUG``
        with the error code only — never the token itself. A disabled
        user still raises, because that is an authorization decision the
        route must not silently ignore.

        Pair it with
        :func:`tempest_fastapi_sdk.auth.guards.require_authenticated` to
        narrow the value back to non-``None`` inside a handler.

        Args:
            credentials (HTTPAuthorizationCredentials | None): The bearer
                credentials extracted by the ``HTTPBearer`` scheme, or
                ``None`` when no ``Authorization`` header was sent.

        Returns:
            FirebaseIdentity | None: The verified identity, or ``None``
            when the request carried no usable token.

        Raises:
            FirebaseUserDisabledError: When the token verifies but the
                Firebase user is disabled (403).
        """
        if credentials is None:
            return None
        try:
            return await self.verify(credentials.credentials)
        except UnauthorizedException as error:
            logger.debug("Firebase soft auth rejected a token: %s", error.code)
            return None

    async def get_uid(
        self,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
        ] = None,
    ) -> str:
        """FastAPI dependency returning only the caller's Firebase uid.

        Most routes want the id, not the whole identity. This depends on
        the bearer credentials directly and calls :meth:`get_identity`
        internally, which avoids referencing ``self`` in a default
        argument — a default is evaluated at method-definition time,
        when the instance does not exist yet.

        Args:
            credentials (HTTPAuthorizationCredentials | None): The bearer
                credentials extracted by the ``HTTPBearer`` scheme, or
                ``None`` when no ``Authorization`` header was sent.

        Returns:
            str: The verified Firebase user id.

        Raises:
            FirebaseTokenMissingError: When no credentials were supplied.
            UnauthorizedException: Any of the verification failures
                documented on :meth:`verify`.
        """
        identity = await self.get_identity(credentials)
        return identity.uid


class FirebaseUserResolver(Generic[UserT]):
    """Turn a verified Firebase identity into the project's user object.

    The SDK does not decide how a ``uid`` becomes a user: that is a
    database lookup, a just-in-time provisioning rule, or a call to
    another service — all of them project decisions. This class is the
    seam. It takes an async resolver and exposes dependencies that yield
    whatever the resolver returns, keeping the concrete user type::

        async def load_user(identity: FirebaseIdentity) -> UserModel | None:
            return await repository.get_by_firebase_uid(identity.uid)

        users: FirebaseUserResolver[UserModel] = FirebaseUserResolver(
            auth, load_user
        )

        @router.get("/profile")
        async def profile(user: UserModel = Depends(users.get_user)) -> UserModel:
            return user

    A resolver that answers ``None`` means "this identity has no user
    here", which is a 401 and not an empty response.

    Attributes:
        auth (FirebaseAuth): The authenticator whose identities are
            resolved.
    """

    def __init__(
        self,
        auth: FirebaseAuth,
        resolver: Callable[[FirebaseIdentity], Awaitable[UserT | None]],
    ) -> None:
        """Initialize the resolver.

        Args:
            auth (FirebaseAuth): The authenticator that verifies tokens.
            resolver (Callable[[FirebaseIdentity], Awaitable[UserT | None]]):
                Async callable mapping a verified identity to the
                project's user object, or ``None`` when no such user
                exists.
        """
        self.auth: FirebaseAuth = auth
        self._resolve: Callable[[FirebaseIdentity], Awaitable[UserT | None]] = resolver

    async def get_user(
        self,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
        ] = None,
    ) -> UserT:
        """FastAPI dependency returning the project's user for the caller.

        Args:
            credentials (HTTPAuthorizationCredentials | None): The bearer
                credentials extracted by the ``HTTPBearer`` scheme, or
                ``None`` when no ``Authorization`` header was sent.

        Returns:
            UserT: Whatever the configured resolver returned.

        Raises:
            FirebaseTokenMissingError: When no credentials were supplied.
            FirebaseTokenInvalidError: When the resolver found no user
                for an otherwise valid identity.
            UnauthorizedException: Any of the verification failures
                documented on :meth:`FirebaseAuth.verify`.
        """
        identity = await self.auth.get_identity(credentials)
        user = await self._resolve(identity)
        if user is None:
            raise FirebaseTokenInvalidError(
                message="No local user is linked to this Firebase account",
                details={"uid": identity.uid},
            )
        return user

    async def get_optional_user(
        self,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
        ] = None,
    ) -> UserT | None:
        """FastAPI dependency returning the user, or ``None``.

        The soft counterpart of :meth:`get_user`: no token, an
        unverifiable token, or an identity with no local user all yield
        ``None`` instead of raising.

        Args:
            credentials (HTTPAuthorizationCredentials | None): The bearer
                credentials extracted by the ``HTTPBearer`` scheme, or
                ``None`` when no ``Authorization`` header was sent.

        Returns:
            UserT | None: The resolved user, or ``None``.

        Raises:
            FirebaseUserDisabledError: When the token verifies but the
                Firebase user is disabled (403).
        """
        identity = await self.auth.get_optional_identity(credentials)
        if identity is None:
            return None
        return await self._resolve(identity)


__all__: list[str] = [
    "DEFAULT_FIREBASE_APP_NAME",
    "FirebaseAuth",
    "FirebaseCredentialError",
    "FirebaseIdentity",
    "FirebaseTokenExpiredError",
    "FirebaseTokenInvalidError",
    "FirebaseTokenMissingError",
    "FirebaseTokenRevokedError",
    "FirebaseUnavailableError",
    "FirebaseUserDisabledError",
    "FirebaseUserResolver",
]
