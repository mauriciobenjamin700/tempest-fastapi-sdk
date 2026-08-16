"""Tests for Firebase ID token verification.

Every test runs offline. Two things make that possible:

* the service account is generated locally (a throwaway RSA key), so
  ``credentials.Certificate`` parses a real key without any Google
  account existing, and
* ``verify_id_token`` is patched on the **real**
  ``firebase_admin.auth`` module, so the error mapping is exercised
  against the genuine exception classes and their genuine inheritance
  (``ExpiredIdTokenError`` and ``RevokedIdTokenError`` both subclass
  ``InvalidIdTokenError``) rather than against stand-ins that could
  agree with a wrong assumption.

One test skips the patch entirely and feeds a malformed token to the
real verifier, which rejects it structurally before any network call.
"""

from __future__ import annotations

import contextlib
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from tempest_fastapi_sdk.api.handlers import register_exception_handlers
from tempest_fastapi_sdk.auth import (
    DEFAULT_FIREBASE_APP_NAME,
    FirebaseAuth,
    FirebaseCredentialError,
    FirebaseIdentity,
    FirebaseTokenExpiredError,
    FirebaseTokenInvalidError,
    FirebaseTokenMissingError,
    FirebaseTokenRevokedError,
    FirebaseUnavailableError,
    FirebaseUserDisabledError,
    FirebaseUserResolver,
)
from tempest_fastapi_sdk.settings import FirebaseSettings

firebase_admin = pytest.importorskip(
    "firebase_admin", reason="needs the optional [firebase] extra"
)
firebase_auth = pytest.importorskip("firebase_admin.auth")

CLAIMS: dict[str, Any] = {
    "uid": "uid-123",
    "sub": "uid-123",
    "email": "person@example.com",
    "email_verified": True,
    "phone_number": "+5511999999999",
    "firebase": {"sign_in_provider": "google.com"},
    "role": "staff",
}


@pytest.fixture(scope="session")
def service_account(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Write a syntactically valid service-account file.

    The private key is generated here, so the file parses as a real
    credential without belonging to any Google project — enough for
    ``initialize_app``, which does not contact Google.

    Args:
        tmp_path_factory (pytest.TempPathFactory): Session-scoped temp
            directory factory.

    Returns:
        Path: The service-account JSON file.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    payload: dict[str, str] = {
        "type": "service_account",
        "project_id": "tempest-test",
        "private_key_id": "test-key",
        "private_key": pem,
        "client_email": "svc@tempest-test.iam.gserviceaccount.com",
        "client_id": "1",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    path = tmp_path_factory.mktemp("firebase") / "service-account.json"
    path.write_text(json.dumps(payload))
    return path


@pytest.fixture
def app_name(request: pytest.FixtureRequest) -> Iterator[str]:
    """Give each test its own Firebase app name, and delete it after.

    Apps live in a module-level registry inside ``firebase_admin``, so a
    leaked app from one test would silently satisfy another test's
    "reuses the existing app" path.

    Args:
        request (pytest.FixtureRequest): The running test, used to build
            a unique name.

    Yields:
        str: The app name to pass to :class:`FirebaseAuth`.
    """
    name = f"tempest-test-{request.node.name}"
    yield name
    with contextlib.suppress(ValueError):
        firebase_admin.delete_app(firebase_admin.get_app(name))


@pytest.fixture
def auth(service_account: Path, app_name: str) -> FirebaseAuth:
    """Build an authenticator backed by the local service account.

    Args:
        service_account (Path): The generated credential file.
        app_name (str): This test's isolated app name.

    Returns:
        FirebaseAuth: The authenticator under test.
    """
    return FirebaseAuth(credentials_path=service_account, app_name=app_name)


def _credentials(token: str = "token") -> HTTPAuthorizationCredentials:
    """Build bearer credentials as ``HTTPBearer`` would.

    Args:
        token (str): The raw token value.

    Returns:
        HTTPAuthorizationCredentials: The credentials a dependency sees.
    """
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _patch_verify(
    monkeypatch: pytest.MonkeyPatch, outcome: Exception | dict[str, Any]
) -> None:
    """Patch ``verify_id_token`` on the real ``firebase_admin.auth``.

    Args:
        monkeypatch (pytest.MonkeyPatch): The patcher.
        outcome (Exception | dict[str, Any]): Raised when an exception,
            returned as the verified claims otherwise.
    """

    def fake_verify(id_token: str, **_: Any) -> dict[str, Any]:
        """Stand in for the real verifier.

        Args:
            id_token (str): The token under verification.
            **_ (Any): The keyword arguments the SDK forwards.

        Returns:
            dict[str, Any]: The configured claims.

        Raises:
            Exception: The configured exception, when one was given.
        """
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(firebase_auth, "verify_id_token", fake_verify)


def test_default_app_name_matches_firebase_admin() -> None:
    """The exported constant tracks ``firebase_admin``'s private default."""
    assert DEFAULT_FIREBASE_APP_NAME == firebase_admin._DEFAULT_APP_NAME


def test_import_without_the_extra_only_fails_on_construction(
    monkeypatch: pytest.MonkeyPatch, service_account: Path
) -> None:
    """Missing extra surfaces at construction, naming the extra to install."""
    monkeypatch.setitem(sys.modules, "firebase_admin", None)
    monkeypatch.setitem(sys.modules, "firebase_admin.auth", None)
    monkeypatch.setitem(sys.modules, "firebase_admin.credentials", None)

    with pytest.raises(ImportError, match=r"\[firebase\] extra"):
        FirebaseAuth(credentials_path=service_account)


def test_second_instance_reuses_the_existing_app(
    service_account: Path, app_name: str
) -> None:
    """Building twice with the same name does not raise, and shares the app."""
    first = FirebaseAuth(credentials_path=service_account, app_name=app_name)
    second = FirebaseAuth(credentials_path=service_account, app_name=app_name)

    assert first._app is second._app


def test_distinct_app_names_get_distinct_apps(
    service_account: Path, app_name: str
) -> None:
    """A second project in the same process gets its own app."""
    first = FirebaseAuth(credentials_path=service_account, app_name=app_name)
    other_name = f"{app_name}-secondary"
    try:
        second = FirebaseAuth(credentials_path=service_account, app_name=other_name)
        assert first._app is not second._app
        assert second._app.name == other_name
    finally:
        firebase_admin.delete_app(firebase_admin.get_app(other_name))


def test_inline_json_credential_is_accepted(
    service_account: Path, app_name: str
) -> None:
    """The service account can arrive as inline JSON instead of a file."""
    auth = FirebaseAuth(
        credentials_json=service_account.read_text(),
        project_id="tempest-test",
        app_name=app_name,
    )

    assert auth._app.project_id == "tempest-test"


def test_malformed_inline_json_raises_credential_error(app_name: str) -> None:
    """Invalid JSON is a configuration failure, not a request failure."""
    with pytest.raises(FirebaseCredentialError, match="not valid JSON"):
        FirebaseAuth(credentials_json="{nope", app_name=app_name)


def test_missing_credential_file_raises_credential_error(
    tmp_path: Path, app_name: str
) -> None:
    """A path that does not exist names the path in the error."""
    missing = tmp_path / "absent.json"

    with pytest.raises(FirebaseCredentialError, match=re.escape("absent.json")):
        FirebaseAuth(credentials_path=missing, app_name=app_name)


async def test_valid_token_becomes_a_typed_identity(
    auth: FirebaseAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verified claims map onto the dataclass, keeping the custom ones."""
    _patch_verify(monkeypatch, CLAIMS)

    identity = await auth.verify("token")

    assert identity == FirebaseIdentity(
        uid="uid-123",
        email="person@example.com",
        email_verified=True,
        phone_number="+5511999999999",
        provider="google.com",
        claims=CLAIMS,
    )
    assert identity.claims["role"] == "staff"


async def test_claims_without_a_subject_are_rejected(
    auth: FirebaseAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claims carrying neither ``uid`` nor ``sub`` cannot authenticate anyone."""
    _patch_verify(monkeypatch, {"email": "person@example.com"})

    with pytest.raises(FirebaseTokenInvalidError):
        await auth.verify("token")


async def test_identity_falls_back_to_sub(auth: FirebaseAuth) -> None:
    """``sub`` is used when ``uid`` is absent from the claims."""
    identity = FirebaseIdentity.from_claims({"sub": "uid-42"})

    assert identity.uid == "uid-42"
    assert identity.provider == ""
    assert identity.email is None


@pytest.mark.parametrize(
    ("raised", "expected", "code"),
    [
        (
            firebase_auth.ExpiredIdTokenError("expired", None),
            FirebaseTokenExpiredError,
            "FIREBASE_TOKEN_EXPIRED",
        ),
        (
            firebase_auth.RevokedIdTokenError("revoked"),
            FirebaseTokenRevokedError,
            "FIREBASE_TOKEN_REVOKED",
        ),
        (
            firebase_auth.InvalidIdTokenError("invalid"),
            FirebaseTokenInvalidError,
            "FIREBASE_TOKEN_INVALID",
        ),
        (
            firebase_auth.CertificateFetchError("no certs", None),
            FirebaseUnavailableError,
            "FIREBASE_UNAVAILABLE",
        ),
        (
            firebase_auth.UserDisabledError("disabled"),
            FirebaseUserDisabledError,
            "FIREBASE_USER_DISABLED",
        ),
    ],
)
async def test_provider_errors_map_to_distinct_sdk_codes(
    auth: FirebaseAuth,
    monkeypatch: pytest.MonkeyPatch,
    raised: Exception,
    expected: type[Exception],
    code: str,
) -> None:
    """Each provider failure gets its own code, not a shared 401.

    Expired and revoked are subclasses of ``InvalidIdTokenError``, so
    this also pins the ``except`` ordering: a most-generic-first version
    of :meth:`FirebaseAuth.verify` collapses the first two rows onto
    ``FIREBASE_TOKEN_INVALID`` and fails here.

    Args:
        auth (FirebaseAuth): The authenticator under test.
        monkeypatch (pytest.MonkeyPatch): The patcher.
        raised (Exception): The ``firebase_admin`` error to simulate.
        expected (type[Exception]): The SDK exception it must become.
        code (str): The machine-readable code clients receive.
    """
    _patch_verify(monkeypatch, raised)

    with pytest.raises(expected) as error:
        await auth.verify("token")

    assert error.value.code == code


async def test_malformed_token_is_rejected_without_network(
    auth: FirebaseAuth,
) -> None:
    """The real verifier rejects a non-JWT before contacting Google.

    Nothing is patched here: ``verify_id_token`` parses the token's
    structure first, so the failure is genuine and offline.
    """
    with pytest.raises(FirebaseTokenInvalidError):
        await auth.verify("not-a-jwt")


async def test_missing_header_raises_its_own_code(auth: FirebaseAuth) -> None:
    """No ``Authorization`` header is distinguishable from a bad token."""
    with pytest.raises(FirebaseTokenMissingError) as error:
        await auth.get_identity(None)

    assert error.value.code == "FIREBASE_TOKEN_MISSING"
    assert error.value.status_code == 401


async def test_get_uid_returns_only_the_subject(
    auth: FirebaseAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The common case yields a plain string."""
    _patch_verify(monkeypatch, CLAIMS)

    assert await auth.get_uid(_credentials()) == "uid-123"


async def test_soft_variant_returns_none_without_a_token(
    auth: FirebaseAuth,
) -> None:
    """A request with no header is anonymous, not unauthorized."""
    assert await auth.get_optional_identity(None) is None


async def test_soft_variant_swallows_an_invalid_token(
    auth: FirebaseAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unusable token is treated as anonymous, matching the SDK's soft rule."""
    _patch_verify(monkeypatch, firebase_auth.ExpiredIdTokenError("expired", None))

    assert await auth.get_optional_identity(_credentials()) is None


async def test_soft_variant_still_raises_for_a_disabled_user(
    auth: FirebaseAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disabled user is a 403 decision the route must not silently drop."""
    _patch_verify(monkeypatch, firebase_auth.UserDisabledError("disabled"))

    with pytest.raises(FirebaseUserDisabledError):
        await auth.get_optional_identity(_credentials())


async def test_resolver_maps_the_identity_to_a_project_user(
    auth: FirebaseAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resolver owns how a uid becomes a user; the SDK only wires it."""
    _patch_verify(monkeypatch, CLAIMS)

    async def load(identity: FirebaseIdentity) -> dict[str, str]:
        """Pretend to load the local user.

        Args:
            identity (FirebaseIdentity): The verified caller.

        Returns:
            dict[str, str]: The project's user object.
        """
        return {"id": f"local-{identity.uid}"}

    users: FirebaseUserResolver[dict[str, str]] = FirebaseUserResolver(auth, load)

    assert await users.get_user(_credentials()) == {"id": "local-uid-123"}


async def test_resolver_returning_none_is_unauthorized(
    auth: FirebaseAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A verified identity with no local user cannot proceed."""
    _patch_verify(monkeypatch, CLAIMS)

    async def load(identity: FirebaseIdentity) -> dict[str, str] | None:
        """Find no local user.

        Args:
            identity (FirebaseIdentity): The verified caller.

        Returns:
            dict[str, str] | None: Always ``None``.
        """
        return None

    users: FirebaseUserResolver[dict[str, str]] = FirebaseUserResolver(auth, load)

    with pytest.raises(FirebaseTokenInvalidError) as error:
        await users.get_user(_credentials())

    assert error.value.details == {"uid": "uid-123"}


async def test_optional_resolver_yields_none_for_anonymous(
    auth: FirebaseAuth,
) -> None:
    """The soft resolver never raises on a missing token."""

    async def load(identity: FirebaseIdentity) -> dict[str, str] | None:
        """Never called in this test.

        Args:
            identity (FirebaseIdentity): The verified caller.

        Returns:
            dict[str, str] | None: Always ``None``.
        """
        return None

    users: FirebaseUserResolver[dict[str, str]] = FirebaseUserResolver(auth, load)

    assert await users.get_optional_user(None) is None


def test_dependency_answers_401_with_the_code_through_fastapi(
    auth: FirebaseAuth, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: the handler turns the exception into 401 plus a code."""
    _patch_verify(monkeypatch, CLAIMS)
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/me")
    async def me(
        identity: FirebaseIdentity = Depends(auth.get_identity),
    ) -> dict[str, str]:
        """Echo the verified caller.

        Args:
            identity (FirebaseIdentity): The verified caller.

        Returns:
            dict[str, str]: The uid the token carried.
        """
        return {"uid": identity.uid}

    client = TestClient(app)

    ok = client.get("/me", headers={"Authorization": "Bearer token"})
    assert ok.status_code == 200
    assert ok.json() == {"uid": "uid-123"}

    anonymous = client.get("/me")
    assert anonymous.status_code == 401
    assert anonymous.json()["code"] == "FIREBASE_TOKEN_MISSING"


def test_settings_drop_empty_values() -> None:
    """An unset variable leaves the constructor default in place."""
    settings = FirebaseSettings()

    assert settings.firebase_kwargs() == {}
    assert settings.enabled is False


def test_settings_prefer_inline_json_over_a_path() -> None:
    """Both channels describe one account; the inline JSON wins."""
    settings = FirebaseSettings(
        FIREBASE_CREDENTIALS_JSON='{"type": "service_account"}',
        FIREBASE_CREDENTIALS_PATH="credentials.json",
        FIREBASE_PROJECT_ID="tempest-test",
    )

    kwargs = settings.firebase_kwargs()

    assert kwargs["credentials_json"] == '{"type": "service_account"}'
    assert kwargs["project_id"] == "tempest-test"
    assert settings.enabled is True
