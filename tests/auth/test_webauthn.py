"""Tests for the bundled WebAuthn / passkey flow.

Every ceremony here runs against `fido2`'s real verification, driven by
the software authenticator in ``webauthn_authenticator.py``. That is the
point: the properties worth testing — a credential bound to one origin,
a challenge usable once, a counter that must advance — are exactly the
ones a mocked verifier would assert away.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    BaseModel,
    BaseUserModel,
    MemoryWebAuthnChallengeStore,
    UserAuthService,
    WebAuthnService,
    make_auth_router,
    make_user_token_model,
    make_web_authn_credential_model,
)
from tempest_fastapi_sdk.exceptions import (
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings
from tests.auth.webauthn_authenticator import SoftwareAuthenticator, b64url

RP_ID: str = "example.com"
ORIGIN: str = "https://example.com"


class _WebAuthnUser(BaseUserModel):
    __tablename__ = "webauthn_test_users"


_WebAuthnUserToken = make_user_token_model(
    user_table="webauthn_test_users",
    tablename="webauthn_test_user_tokens",
    class_name="_WebAuthnUserToken",
)

_WebAuthnCredential = make_web_authn_credential_model(
    user_table="webauthn_test_users",
    tablename="webauthn_test_credentials",
    class_name="_WebAuthnCredential",
)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _auth_settings(**overrides: Any) -> AuthSettings:
    """Build settings with WebAuthn on and the test relying party.

    Args:
        **overrides (Any): Fields to override.

    Returns:
        AuthSettings: The settings instance.
    """
    values: dict[str, Any] = {
        "AUTH_AUTO_ACTIVATE": True,
        "AUTH_WEBAUTHN_ENABLED": True,
        "AUTH_WEBAUTHN_RP_ID": RP_ID,
        "AUTH_WEBAUTHN_RP_NAME": "Tempest Test",
    }
    values.update(overrides)
    return AuthSettings(**values)


def _services(
    settings: AuthSettings | None = None,
) -> tuple[UserAuthService, WebAuthnService]:
    """Build the auth service and the WebAuthn service over it.

    Args:
        settings (AuthSettings | None): Override the defaults.

    Returns:
        tuple[UserAuthService, WebAuthnService]: The pair.
    """
    auth = settings or _auth_settings()
    jwt = JWTSettings(JWT_SECRET="x" * 32)
    service = UserAuthService(
        user_model=_WebAuthnUser,
        token_model=_WebAuthnUserToken,  # type: ignore[arg-type]
        auth_settings=auth,
        jwt_settings=jwt,
        email=None,
    )
    webauthn = WebAuthnService(
        user_model=_WebAuthnUser,
        credential_model=_WebAuthnCredential,
        auth_settings=auth,
        challenge_store=MemoryWebAuthnChallengeStore(),
    )
    return service, webauthn


async def _make_user(
    service: UserAuthService,
    session: AsyncSession,
    *,
    email: str = "passkey@example.com",
) -> Any:
    """Create an active user.

    Args:
        service (UserAuthService): The auth service.
        session (AsyncSession): Active session.
        email (str): Account email.

    Returns:
        Any: The persisted user.
    """
    user, _ = await service.signup(
        session,
        email=email,
        password="strong-pass-12-chars",
    )
    await session.commit()
    return user


async def _register(
    webauthn: WebAuthnService,
    session: AsyncSession,
    user: Any,
    *,
    authenticator: SoftwareAuthenticator | None = None,
    name: str | None = None,
) -> tuple[SoftwareAuthenticator, Any]:
    """Run a full registration ceremony.

    Args:
        webauthn (WebAuthnService): The service under test.
        session (AsyncSession): Active session.
        user (Any): The account registering.
        authenticator (SoftwareAuthenticator | None): Reuse an existing
            device instead of creating one.
        name (str | None): Label for the credential.

    Returns:
        tuple[SoftwareAuthenticator, Any]: The device and the stored row.
    """
    device = authenticator or SoftwareAuthenticator(rp_id=RP_ID)
    options, challenge_id = await webauthn.register_begin(session, user=user)
    record = await webauthn.register_complete(
        session,
        user=user,
        challenge_id=challenge_id,
        response=device.register(options, origin=ORIGIN),
        name=name,
    )
    await session.commit()
    return device, record


class TestRegistration:
    async def test_registers_and_stores_the_credential(
        self,
        session: AsyncSession,
    ) -> None:
        """A verified attestation becomes a row bound to the user."""
        service, webauthn = _services()
        user = await _make_user(service, session)
        device, record = await _register(webauthn, session, user, name="YubiKey")
        assert record.user_id == user.id
        assert record.credential_id == device.credential_id
        assert record.name == "YubiKey"
        assert record.transports == "usb"
        assert record.aaguid == device.aaguid.hex()
        assert record.backed_up is False

    async def test_records_a_synced_passkey_as_backed_up(
        self,
        session: AsyncSession,
    ) -> None:
        """The backup flag decides whether recovery is mandatory."""
        service, webauthn = _services()
        user = await _make_user(session=session, service=service)
        device = SoftwareAuthenticator(rp_id=RP_ID, backed_up=True)
        _, record = await _register(webauthn, session, user, authenticator=device)
        assert record.backed_up is True

    async def test_excludes_already_registered_credentials(
        self,
        session: AsyncSession,
    ) -> None:
        """The second ceremony tells the device what it already holds."""
        service, webauthn = _services()
        user = await _make_user(service, session)
        device, _ = await _register(webauthn, session, user)
        options, _ = await webauthn.register_begin(session, user=user)
        excluded = options["publicKey"]["excludeCredentials"]
        assert [item["id"] for item in excluded] == [b64url(device.credential_id)]

    async def test_rejects_a_credential_registered_elsewhere(
        self,
        session: AsyncSession,
    ) -> None:
        """A credential ID is unique across the table, not per account."""
        service, webauthn = _services()
        first = await _make_user(service, session, email="a@example.com")
        second = await _make_user(service, session, email="b@example.com")
        device, _ = await _register(webauthn, session, first)
        options, challenge_id = await webauthn.register_begin(session, user=second)
        with pytest.raises(ValidationException, match="already registered"):
            await webauthn.register_complete(
                session,
                user=second,
                challenge_id=challenge_id,
                response=device.register(options, origin=ORIGIN),
            )

    async def test_rejects_an_attestation_from_another_origin(
        self,
        session: AsyncSession,
    ) -> None:
        """Origin binding is the whole point — a lookalike must fail."""
        service, webauthn = _services()
        user = await _make_user(service, session)
        device = SoftwareAuthenticator(rp_id=RP_ID)
        options, challenge_id = await webauthn.register_begin(session, user=user)
        with pytest.raises(UnauthorizedException):
            await webauthn.register_complete(
                session,
                user=user,
                challenge_id=challenge_id,
                response=device.register(
                    options,
                    origin="https://example.com.evil.test",
                ),
            )

    async def test_rejects_an_unknown_challenge(
        self,
        session: AsyncSession,
    ) -> None:
        """No state, no ceremony."""
        service, webauthn = _services()
        user = await _make_user(service, session)
        device = SoftwareAuthenticator(rp_id=RP_ID)
        options, challenge_id = await webauthn.register_begin(session, user=user)
        response = device.register(options, origin=ORIGIN)
        await webauthn.register_complete(
            session,
            user=user,
            challenge_id=challenge_id,
            response=response,
        )
        with pytest.raises(UnauthorizedException, match="unknown or expired"):
            await webauthn.register_complete(
                session,
                user=user,
                challenge_id=challenge_id,
                response=response,
            )


class TestAuthentication:
    async def test_passwordless_login_returns_the_user(
        self,
        session: AsyncSession,
    ) -> None:
        """A valid assertion authenticates without the account being named."""
        service, webauthn = _services()
        user = await _make_user(service, session)
        device, _ = await _register(webauthn, session, user)
        options, challenge_id = await webauthn.authenticate_begin(session)
        assert not options["publicKey"].get("allowCredentials")
        authenticated = await webauthn.authenticate_complete(
            session,
            challenge_id=challenge_id,
            response=device.authenticate(
                options,
                origin=ORIGIN,
                user_handle=user.id.bytes,
            ),
        )
        assert authenticated.id == user.id
        assert authenticated.last_login_at is not None

    async def test_email_narrows_the_allow_list(
        self,
        session: AsyncSession,
    ) -> None:
        """Naming the account offers its credentials to the browser."""
        service, webauthn = _services()
        user = await _make_user(service, session)
        device, _ = await _register(webauthn, session, user)
        options, _ = await webauthn.authenticate_begin(session, email=user.email)
        allowed = options["publicKey"]["allowCredentials"]
        assert [item["id"] for item in allowed] == [b64url(device.credential_id)]

    async def test_unknown_email_does_not_leak(
        self,
        session: AsyncSession,
    ) -> None:
        """An address nobody registered answers like one that exists."""
        service, webauthn = _services()
        await _make_user(service, session)
        known, _ = await webauthn.authenticate_begin(
            session,
            email="nobody@example.com",
        )
        assert known["publicKey"]["challenge"]
        assert not known["publicKey"].get("allowCredentials")

    async def test_rejects_a_replayed_challenge(
        self,
        session: AsyncSession,
    ) -> None:
        """The state is popped on use, so a captured response is spent."""
        service, webauthn = _services()
        user = await _make_user(service, session)
        device, _ = await _register(webauthn, session, user)
        options, challenge_id = await webauthn.authenticate_begin(session)
        response = device.authenticate(options, origin=ORIGIN)
        await webauthn.authenticate_complete(
            session,
            challenge_id=challenge_id,
            response=response,
        )
        with pytest.raises(UnauthorizedException, match="unknown or expired"):
            await webauthn.authenticate_complete(
                session,
                challenge_id=challenge_id,
                response=response,
            )

    async def test_rejects_a_counter_that_did_not_advance(
        self,
        session: AsyncSession,
    ) -> None:
        """A stalled counter is the spec's cloned-authenticator signal."""
        service, webauthn = _services()
        user = await _make_user(service, session)
        device, _ = await _register(webauthn, session, user)
        options, challenge_id = await webauthn.authenticate_begin(session)
        await webauthn.authenticate_complete(
            session,
            challenge_id=challenge_id,
            response=device.authenticate(options, origin=ORIGIN),
        )
        options, challenge_id = await webauthn.authenticate_begin(session)
        with pytest.raises(UnauthorizedException, match="counter did not advance"):
            await webauthn.authenticate_complete(
                session,
                challenge_id=challenge_id,
                response=device.authenticate(
                    options,
                    origin=ORIGIN,
                    advance_counter=False,
                ),
            )

    async def test_accepts_an_authenticator_that_always_reports_zero(
        self,
        session: AsyncSession,
    ) -> None:
        """Most platform passkeys never count — zero carries no signal."""
        service, webauthn = _services()
        user = await _make_user(service, session)
        device = SoftwareAuthenticator(rp_id=RP_ID)
        await _register(webauthn, session, user, authenticator=device)
        for _ in range(2):
            options, challenge_id = await webauthn.authenticate_begin(session)
            authenticated = await webauthn.authenticate_complete(
                session,
                challenge_id=challenge_id,
                response=device.authenticate(
                    options,
                    origin=ORIGIN,
                    advance_counter=False,
                ),
            )
            assert authenticated.id == user.id

    async def test_rejects_an_unregistered_credential(
        self,
        session: AsyncSession,
    ) -> None:
        """A key nobody registered has no account to authenticate."""
        service, webauthn = _services()
        await _make_user(service, session)
        stranger = SoftwareAuthenticator(rp_id=RP_ID)
        options, challenge_id = await webauthn.authenticate_begin(session)
        with pytest.raises(UnauthorizedException, match="unknown credential"):
            await webauthn.authenticate_complete(
                session,
                challenge_id=challenge_id,
                response=stranger.authenticate(options, origin=ORIGIN),
            )

    async def test_rejects_an_inactive_account(
        self,
        session: AsyncSession,
    ) -> None:
        """Deactivating the user must close the passkey path too."""
        service, webauthn = _services()
        user = await _make_user(service, session)
        device, _ = await _register(webauthn, session, user)
        user.is_active = False
        await session.commit()
        options, challenge_id = await webauthn.authenticate_begin(session)
        with pytest.raises(UnauthorizedException, match="not active"):
            await webauthn.authenticate_complete(
                session,
                challenge_id=challenge_id,
                response=device.authenticate(options, origin=ORIGIN),
            )


class TestCredentialManagement:
    async def test_lists_and_deletes(self, session: AsyncSession) -> None:
        """Listing is per user; deleting is scoped to the owner."""
        service, webauthn = _services()
        user = await _make_user(service, session)
        device, _ = await _register(webauthn, session, user, name="Key A")
        assert await webauthn.list_credentials(session, user=user) != []
        await webauthn.delete_credential(
            session,
            user=user,
            credential_id=device.credential_id,
        )
        await session.commit()
        assert await webauthn.list_credentials(session, user=user) == []

    async def test_cannot_delete_somebody_elses_credential(
        self,
        session: AsyncSession,
    ) -> None:
        """A foreign ID answers exactly like one that does not exist."""
        service, webauthn = _services()
        owner = await _make_user(service, session, email="owner@example.com")
        other = await _make_user(service, session, email="other@example.com")
        device, _ = await _register(webauthn, session, owner)
        with pytest.raises(NotFoundException):
            await webauthn.delete_credential(
                session,
                user=other,
                credential_id=device.credential_id,
            )
        assert await webauthn.list_credentials(session, user=owner) != []


class TestConfiguration:
    def test_refuses_an_empty_relying_party(self) -> None:
        """A relying party with no identity binds credentials to nothing."""
        with pytest.raises(ValueError, match="AUTH_WEBAUTHN_RP_ID"):
            WebAuthnService(
                user_model=_WebAuthnUser,
                credential_model=_WebAuthnCredential,
                auth_settings=_auth_settings(AUTH_WEBAUTHN_RP_ID=""),
            )

    async def test_allowed_origins_replace_the_default_rule(
        self,
        session: AsyncSession,
    ) -> None:
        """A configured list is the whole allowlist, dev origins included."""
        settings = _auth_settings(
            AUTH_WEBAUTHN_ALLOWED_ORIGINS=["http://localhost:5173"],
        )
        service, webauthn = _services(settings)
        user = await _make_user(service, session)
        device = SoftwareAuthenticator(rp_id=RP_ID)
        options, challenge_id = await webauthn.register_begin(session, user=user)
        record = await webauthn.register_complete(
            session,
            user=user,
            challenge_id=challenge_id,
            response=device.register(options, origin="http://localhost:5173"),
        )
        assert record.credential_id == device.credential_id

        options, challenge_id = await webauthn.register_begin(session, user=user)
        with pytest.raises(UnauthorizedException):
            await webauthn.register_complete(
                session,
                user=user,
                challenge_id=challenge_id,
                response=SoftwareAuthenticator(rp_id=RP_ID).register(
                    options,
                    origin=ORIGIN,
                ),
            )


class TestChallengeStore:
    async def test_state_is_single_use(self) -> None:
        """Popping twice yields the state once."""
        store = MemoryWebAuthnChallengeStore()
        await store.put("k", {"challenge": "x"}, 60)
        assert await store.pop("k") == {"challenge": "x"}
        assert await store.pop("k") is None

    async def test_expired_state_is_gone(self) -> None:
        """A ceremony left open past its TTL cannot be completed."""
        store = MemoryWebAuthnChallengeStore()
        await store.put("k", {"challenge": "x"}, 0)
        assert await store.pop("k") is None


class TestRouter:
    async def test_full_ceremony_over_http(self, session: AsyncSession) -> None:
        """Register then log in passwordlessly, through the endpoints."""
        service, webauthn = _services()
        user = await _make_user(service, session)
        access, _ = await service.issue_token_pair(session, user)
        await session.commit()

        async def _session_factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(
            make_auth_router(
                service,
                session_factory=_session_factory,
                webauthn=webauthn,
            ),
        )
        transport = ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {access}"}
        device = SoftwareAuthenticator(rp_id=RP_ID)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            begin = await client.post("/auth/webauthn/register/begin", headers=headers)
            assert begin.status_code == 200
            payload = begin.json()
            complete = await client.post(
                "/auth/webauthn/register/complete",
                headers=headers,
                json={
                    "challenge_id": payload["challenge_id"],
                    "credential": device.register(payload["options"], origin=ORIGIN),
                    "name": "Test key",
                },
            )
            assert complete.status_code == 200
            credential_id = complete.json()["credential_id"]
            assert credential_id == b64url(device.credential_id)

            listing = await client.get("/auth/webauthn/credentials", headers=headers)
            assert [row["name"] for row in listing.json()] == ["Test key"]

            login_begin = await client.post(
                "/auth/webauthn/authenticate/begin",
                json={},
            )
            assert login_begin.status_code == 200
            login_payload = login_begin.json()
            login = await client.post(
                "/auth/webauthn/authenticate/complete",
                json={
                    "challenge_id": login_payload["challenge_id"],
                    "credential": device.authenticate(
                        login_payload["options"],
                        origin=ORIGIN,
                    ),
                },
            )
            assert login.status_code == 200
            assert login.json()["access_token"]

            removed = await client.post(
                "/auth/webauthn/credentials/delete",
                headers=headers,
                json={"credential_id": credential_id},
            )
            assert removed.status_code == 204
            empty = await client.get("/auth/webauthn/credentials", headers=headers)
            assert empty.json() == []

    async def test_endpoints_absent_when_disabled(
        self,
        session: AsyncSession,
    ) -> None:
        """The kill-switch removes the routes entirely."""
        settings = _auth_settings(AUTH_WEBAUTHN_ENABLED=False)
        service, webauthn = _services(settings)

        async def _session_factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(
            make_auth_router(
                service,
                session_factory=_session_factory,
                webauthn=webauthn,
            ),
        )
        paths = {getattr(route, "path", "") for route in app.routes}
        assert not any(path.startswith("/auth/webauthn") for path in paths)

    async def test_enabling_without_a_service_fails_at_wiring(
        self,
        session: AsyncSession,
    ) -> None:
        """A missing service must not become a 500 per request."""
        service, _ = _services()

        async def _session_factory() -> AsyncIterator[AsyncSession]:
            yield session

        with pytest.raises(RuntimeError, match="WebAuthnService"):
            make_auth_router(service, session_factory=_session_factory)

    async def test_malformed_credential_id_is_a_validation_error(
        self,
        session: AsyncSession,
    ) -> None:
        """Garbage in the delete body is a validation error, not a 404."""
        service, webauthn = _services()
        user = await _make_user(service, session)
        access, _ = await service.issue_token_pair(session, user)
        await session.commit()

        async def _session_factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(
            make_auth_router(
                service,
                session_factory=_session_factory,
                webauthn=webauthn,
            ),
        )
        from tempest_fastapi_sdk import register_exception_handlers

        register_exception_handlers(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post(
                "/auth/webauthn/credentials/delete",
                headers={"Authorization": f"Bearer {access}"},
                json={"credential_id": "not base64!!"},
            )
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"
