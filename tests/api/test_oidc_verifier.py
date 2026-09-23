"""``OIDCTokenVerifier`` — offline verification of realm-signed tokens.

The cryptography is not what these tests are about. What each service used
to get wrong is the **status** each failure maps to: a token that names a
key nobody published is the caller's fault (401, never retry), and a realm
that cannot be reached is not (502, retry later). A naive
``except PyJWKClientError: raise Unavailable`` answered 502 for the first
and 500 for a token that is not a JWT at all — the classes below pin the
mapping one situation at a time.

The key set is served through an ``httpx.MockTransport`` injected into the
``HTTPClient``, so nothing touches the network and every fetch is counted.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from tempest_fastapi_sdk import (
    HTTPClient,
    OAuthProviderUnavailableException,
    OAuthTokenAudienceMismatchException,
    OAuthTokenRejectedException,
    OIDCTokenVerifier,
    RetryPolicy,
)
from tempest_fastapi_sdk.api.oidc_verifier import ALWAYS_REQUIRED_CLAIMS

ISSUER: str = "https://id.test/realms/app"
JWKS_URL: str = f"{ISSUER}/protocol/openid-connect/certs"
CLIENT_ID: str = "mobile-app"


def _private_key() -> rsa.RSAPrivateKey:
    """Generate a fresh RSA signing key.

    Returns:
        rsa.RSAPrivateKey: A 2048-bit key.
    """
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
    """Publish the public half of ``key`` the way a realm does.

    Args:
        key (rsa.RSAPrivateKey): The signing key.
        kid (str): Key id.

    Returns:
        dict[str, Any]: One JWK entry.
    """
    entry: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    entry.update(kid=kid, alg="RS256", use="sig")
    return entry


def _claims(**overrides: Any) -> dict[str, Any]:
    """Build the claims of a legitimate Keycloak-shaped access token.

    ``aud`` is ``"account"`` and the client is named in ``azp``, which is
    what a realm access token carries.

    Args:
        **overrides (Any): Claim overrides; a ``None`` value drops it.

    Returns:
        dict[str, Any]: The claims.
    """
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "sub": "user-1",
        "aud": ["account"],
        "azp": CLIENT_ID,
        "exp": now + 300,
        "iat": now,
        "email": "ana@example.com",
        "email_verified": True,
        "name": "Ana Souza",
    }
    claims.update(overrides)
    return {key: value for key, value in claims.items() if value is not None}


def _sign(
    key: rsa.RSAPrivateKey,
    kid: str = "k1",
    *,
    algorithm: str = "RS256",
    **claims: Any,
) -> str:
    """Sign a token with ``key``.

    Args:
        key (rsa.RSAPrivateKey): The signing key.
        kid (str): Key id to put in the header.
        algorithm (str): Signing algorithm.
        **claims (Any): Claim overrides.

    Returns:
        str: The compact JWT.
    """
    return jwt.encode(_claims(**claims), key, algorithm=algorithm, headers={"kid": kid})


class _Realm:
    """A fake realm: serves a mutable key set and counts fetches.

    Attributes:
        keys (list[dict[str, Any]]): The JWK entries currently published.
        fetches (int): How many times the key set was requested.
        responder (Callable[[httpx.Request], httpx.Response] | None):
            Overrides the answer (outage, 500, garbage).
    """

    def __init__(self, *keys: dict[str, Any]) -> None:
        """Initialize.

        Args:
            *keys (dict[str, Any]): The JWK entries to publish.
        """
        self.keys: list[dict[str, Any]] = list(keys)
        self.fetches: int = 0
        self.responder: Callable[[httpx.Request], httpx.Response] | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Answer one key-set request.

        Args:
            request (httpx.Request): The outbound request.

        Returns:
            httpx.Response: The key set, or the configured override.
        """
        self.fetches += 1
        if self.responder is not None:
            return self.responder(request)
        return httpx.Response(200, json={"keys": self.keys})


def _verifier(realm: _Realm, **kwargs: Any) -> OIDCTokenVerifier:
    """Build a verifier whose key-set fetch goes to ``realm``.

    Retries are off so an outage answers on the first attempt.

    Args:
        realm (_Realm): The fake realm.
        **kwargs (Any): Extra constructor keywords.

    Returns:
        OIDCTokenVerifier: The verifier under test.
    """
    http = HTTPClient(
        failure_threshold=0,
        retry_policy=RetryPolicy(max_attempts=1),
        transport=httpx.MockTransport(realm.handle),
    )
    return OIDCTokenVerifier(ISSUER, JWKS_URL, http_client=http, **kwargs)


async def _verify(verifier: OIDCTokenVerifier, token: str) -> dict[str, Any]:
    """Verify ``token`` for :data:`CLIENT_ID`.

    Args:
        verifier (OIDCTokenVerifier): The verifier under test.
        token (str): The compact JWT.

    Returns:
        dict[str, Any]: The verified claims.
    """
    return await verifier.verify(token, accepted_audiences=(CLIENT_ID,))


@pytest.fixture
def key() -> rsa.RSAPrivateKey:
    """The realm's current signing key."""
    return _private_key()


@pytest.fixture
def realm(key: rsa.RSAPrivateKey) -> _Realm:
    """A realm publishing ``key`` as ``k1``."""
    return _Realm(_jwk(key, "k1"))


class TestALegitimateTokenVerifies:
    """The happy path, Keycloak-shaped."""

    async def test_claims_come_back(
        self, realm: _Realm, key: rsa.RSAPrivateKey
    ) -> None:
        claims = await _verify(_verifier(realm), _sign(key))

        assert claims["sub"] == "user-1"
        assert claims["azp"] == CLIENT_ID

    async def test_account_audience_with_our_azp_is_accepted(
        self, realm: _Realm, key: rsa.RSAPrivateKey
    ) -> None:
        claims = await _verify(_verifier(realm), _sign(key, aud=["account"]))

        assert claims["aud"] == ["account"]


class TestAnUnknownKidIsTheCallersFault:
    """The key set was read; it just does not have that key."""

    async def test_it_answers_401_not_502(self, realm: _Realm) -> None:
        stranger = _private_key()

        with pytest.raises(OAuthTokenRejectedException) as caught:
            await _verify(_verifier(realm), _sign(stranger, kid="xx"))

        assert caught.value.status_code == 401
        assert caught.value.code == "OAUTH_TOKEN_REJECTED"
        assert realm.fetches == 1


class TestSomethingThatIsNotAJwtIsRefused:
    """``"x"`` in the field is a bad credential, not a server error."""

    @pytest.mark.parametrize("token", ["x", "a.b.c", "", "e30.e30"])
    async def test_it_answers_401(self, realm: _Realm, token: str) -> None:
        with pytest.raises(OAuthTokenRejectedException) as caught:
            await _verify(_verifier(realm), token)

        assert caught.value.status_code == 401
        assert realm.fetches == 0

    async def test_a_token_without_kid_is_refused(
        self, realm: _Realm, key: rsa.RSAPrivateKey
    ) -> None:
        token = jwt.encode(_claims(), key, algorithm="RS256")

        with pytest.raises(OAuthTokenRejectedException):
            await _verify(_verifier(realm), token)
        assert realm.fetches == 0


class TestAnUnreachableRealmIsTheOnlyRetryable502:
    """Outage, error status and garbage all mean "try again later"."""

    async def test_transport_down_answers_502(
        self, realm: _Realm, key: rsa.RSAPrivateKey
    ) -> None:
        def _down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        realm.responder = _down

        with pytest.raises(OAuthProviderUnavailableException) as caught:
            await _verify(_verifier(realm), _sign(key))

        assert caught.value.status_code == 502
        assert caught.value.code == "OAUTH_PROVIDER_UNAVAILABLE"

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(500, text="boom"),
            httpx.Response(404, text="no realm"),
            httpx.Response(200, text="<html>not json</html>"),
            httpx.Response(200, json={"keys": "abc"}),
            httpx.Response(200, json=[1, 2]),
            httpx.Response(200, json={"keys": []}),
            httpx.Response(200, json={"keys": [{"kty": "RSA", "kid": "k1"}]}),
        ],
        ids=["500", "404", "html", "keys-str", "list", "empty", "broken-key"],
    )
    async def test_an_unusable_key_set_answers_502(
        self,
        realm: _Realm,
        key: rsa.RSAPrivateKey,
        response: httpx.Response,
    ) -> None:
        realm.responder = lambda request: response

        with pytest.raises(OAuthProviderUnavailableException) as caught:
            await _verify(_verifier(realm), _sign(key))

        assert caught.value.status_code == 502

    async def test_an_encryption_only_key_set_answers_502(
        self, realm: _Realm, key: rsa.RSAPrivateKey
    ) -> None:
        entry = _jwk(key, "k1")
        entry["use"] = "enc"
        realm.keys = [entry]

        with pytest.raises(OAuthProviderUnavailableException):
            await _verify(_verifier(realm), _sign(key))

    async def test_the_outage_is_remembered_during_the_cooldown(
        self, realm: _Realm, key: rsa.RSAPrivateKey
    ) -> None:
        realm.responder = lambda request: httpx.Response(503)
        verifier = _verifier(realm)

        for _ in range(3):
            with pytest.raises(OAuthProviderUnavailableException):
                await _verify(verifier, _sign(key))

        assert realm.fetches == 1


class TestATokenWithoutExpIsRefused:
    """PyJWT checks ``exp`` only when present; the verifier requires it."""

    async def test_missing_exp_answers_401(
        self, realm: _Realm, key: rsa.RSAPrivateKey
    ) -> None:
        with pytest.raises(OAuthTokenRejectedException) as caught:
            await _verify(_verifier(realm), _sign(key, exp=None))

        assert caught.value.details["reason"] == "MissingRequiredClaimError"

    async def test_exp_stays_required_when_the_caller_omits_it(
        self, realm: _Realm, key: rsa.RSAPrivateKey
    ) -> None:
        verifier = _verifier(realm, required_claims=("email",))

        assert set(ALWAYS_REQUIRED_CLAIMS) <= set(verifier.required_claims)
        with pytest.raises(OAuthTokenRejectedException):
            await _verify(verifier, _sign(key, exp=None))

    async def test_an_expired_token_answers_401(
        self, realm: _Realm, key: rsa.RSAPrivateKey
    ) -> None:
        with pytest.raises(OAuthTokenRejectedException):
            await _verify(_verifier(realm), _sign(key, exp=int(time.time()) - 10))


class TestTheAlgorithmListIsClosed:
    """The header is attacker-controlled; ``alg`` is never trusted."""

    async def test_hs256_is_refused_before_any_fetch(
        self, realm: _Realm, key: rsa.RSAPrivateKey
    ) -> None:
        token = jwt.encode(
            _claims(),
            "a-shared-secret-at-least-32-bytes-long",
            algorithm="HS256",
            headers={"kid": "k1"},
        )

        with pytest.raises(OAuthTokenRejectedException) as caught:
            await _verify(_verifier(realm), token)

        assert caught.value.details["reason"] == "algorithm"
        assert realm.fetches == 0

    async def test_none_is_refused_before_any_fetch(self, realm: _Realm) -> None:
        token = jwt.encode(_claims(), None, algorithm="none", headers={"kid": "k1"})

        with pytest.raises(OAuthTokenRejectedException):
            await _verify(_verifier(realm), token)
        assert realm.fetches == 0

    async def test_none_cannot_be_opted_into(self, realm: _Realm) -> None:
        verifier = _verifier(realm, algorithms=("RS256", "none"))

        assert verifier.algorithms == ("RS256",)
        with pytest.raises(ValueError):
            _verifier(realm, algorithms=("none",))


class TestAnotherClientsTokenIsAnAudienceMismatch:
    """``aud`` + ``azp`` are read together, as the provider clients do."""

    async def test_account_aud_with_foreign_azp_is_refused(
        self, realm: _Realm, key: rsa.RSAPrivateKey
    ) -> None:
        with pytest.raises(OAuthTokenAudienceMismatchException) as caught:
            await _verify(_verifier(realm), _sign(key, aud=["account"], azp="other"))

        assert caught.value.status_code == 401
        assert caught.value.code == "OAUTH_TOKEN_AUDIENCE_MISMATCH"


class TestForgeriesAreRefused:
    """Wrong issuer, wrong key under a known ``kid``."""

    async def test_wrong_issuer_answers_401(
        self, realm: _Realm, key: rsa.RSAPrivateKey
    ) -> None:
        with pytest.raises(OAuthTokenRejectedException) as caught:
            await _verify(
                _verifier(realm), _sign(key, iss="https://evil.test/realms/app")
            )

        assert caught.value.details["reason"] == "InvalidIssuerError"

    async def test_foreign_key_under_a_known_kid_answers_401(
        self, realm: _Realm
    ) -> None:
        forger = _private_key()

        with pytest.raises(OAuthTokenRejectedException) as caught:
            await _verify(_verifier(realm), _sign(forger, kid="k1"))

        assert caught.value.details["reason"] == "InvalidSignatureError"


class TestTheKeySetIsCached:
    """One fetch serves every login until the lifespan runs out."""

    async def test_many_tokens_cost_one_fetch(
        self, realm: _Realm, key: rsa.RSAPrivateKey
    ) -> None:
        verifier = _verifier(realm)

        for index in range(10):
            await _verify(verifier, _sign(key, sub=f"user-{index}"))

        assert realm.fetches == 1

    async def test_concurrent_first_logins_share_one_fetch(
        self, realm: _Realm, key: rsa.RSAPrivateKey
    ) -> None:
        verifier = _verifier(realm)

        await asyncio.gather(*(_verify(verifier, _sign(key)) for _ in range(20)))

        assert realm.fetches == 1

    async def test_the_set_is_refreshed_after_its_lifespan(
        self, realm: _Realm, key: rsa.RSAPrivateKey
    ) -> None:
        verifier = _verifier(realm)
        clock = [1000.0]
        verifier._clock = lambda: clock[0]

        await _verify(verifier, _sign(key))
        clock[0] += 301
        await _verify(verifier, _sign(key))

        assert realm.fetches == 2


class TestForgedKidsCannotAmplifyTraffic:
    """A miss refreshes at most once per ``min_refresh_interval``."""

    async def test_five_forged_kids_cost_one_refresh(
        self, realm: _Realm, key: rsa.RSAPrivateKey
    ) -> None:
        verifier = _verifier(realm)
        clock = [1000.0]
        verifier._clock = lambda: clock[0]
        await _verify(verifier, _sign(key))
        clock[0] += 61
        forger = _private_key()

        for index in range(5):
            with pytest.raises(OAuthTokenRejectedException):
                await _verify(verifier, _sign(forger, kid=f"forged-{index}"))

        assert realm.fetches == 2

    async def test_forged_kids_inside_the_cooldown_cost_nothing(
        self, realm: _Realm, key: rsa.RSAPrivateKey
    ) -> None:
        verifier = _verifier(realm)
        await _verify(verifier, _sign(key))
        forger = _private_key()

        for index in range(5):
            with pytest.raises(OAuthTokenRejectedException):
                await _verify(verifier, _sign(forger, kid=f"forged-{index}"))

        assert realm.fetches == 1

    async def test_a_rotated_key_is_picked_up_after_the_cooldown(
        self, realm: _Realm, key: rsa.RSAPrivateKey
    ) -> None:
        verifier = _verifier(realm)
        clock = [1000.0]
        verifier._clock = lambda: clock[0]
        await _verify(verifier, _sign(key))
        rotated = _private_key()
        realm.keys = [_jwk(rotated, "k2")]

        clock[0] += 10
        with pytest.raises(OAuthTokenRejectedException):
            await _verify(verifier, _sign(rotated, kid="k2"))
        assert realm.fetches == 1
        clock[0] += 51
        claims = await _verify(verifier, _sign(rotated, kid="k2"))

        assert claims["sub"] == "user-1"
        assert realm.fetches == 2


class TestTheExtraIsRequired:
    """Without ``cryptography`` the constructor names the extra."""

    def test_missing_crypto_raises_import_error_naming_oidc(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tempest_fastapi_sdk.api import oidc_verifier

        monkeypatch.setattr(oidc_verifier, "_has_crypto", False)

        with pytest.raises(ImportError, match=r"\[oidc\]"):
            OIDCTokenVerifier(ISSUER, JWKS_URL, http_client=HTTPClient())
