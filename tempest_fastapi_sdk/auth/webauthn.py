"""WebAuthn / passkey registration and login over ``fido2``.

TOTP proves the user holds a shared secret; a phishing page that
forwards the code in real time defeats it. WebAuthn binds the assertion
to the **origin** that requested it, so a credential registered for
``app.example.com`` produces nothing a page on ``app-example.com`` can
use. That is the property this module exists for.

Three pieces:

* :class:`WebAuthnChallengeStore` — where the between-request state of a
  ceremony lives. It is **single use**: a challenge is popped when
  verified, so a captured response cannot be replayed.
* :class:`WebAuthnService` — the four ceremony halves (register begin /
  complete, authenticate begin / complete) plus credential listing and
  removal, over the project's concrete credential model.
* Origin verification — configured from ``AUTH_WEBAUTHN_*``. Getting
  this wrong is the one mistake that costs the phishing resistance, so
  the settings make the choice explicit rather than inferring it.

Needs the ``[webauthn]`` extra (``fido2``). The module imports without
it; the import happens inside :class:`WebAuthnService`, so a project
that never builds one pays nothing.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from sqlalchemy import select

from tempest_fastapi_sdk.exceptions import (
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)
from tempest_fastapi_sdk.utils.datetime import utcnow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.db.user_model import BaseUserModel
    from tempest_fastapi_sdk.db.user_webauthn_credential_model import (
        BaseWebAuthnCredentialModel,
    )
    from tempest_fastapi_sdk.settings.mixins import AuthSettings

CHALLENGE_ID_BYTES: int = 16
"""Entropy of the handle naming a ceremony in the challenge store.

The handle is not a secret — it only names a pending challenge, which
is itself verified — but it must be unguessable enough that one caller
cannot pop another's state. 128 bits matches the SDK's other opaque
identifiers.
"""


@runtime_checkable
class WebAuthnChallengeStore(Protocol):
    """Holds the state of a ceremony between its two requests.

    ``fido2`` produces an opaque state dict at *begin* and consumes it
    at *complete*. It must survive the round trip to the browser without
    the client being able to alter it — hence server-side storage rather
    than a cookie — and must be usable exactly once.
    """

    async def put(self, challenge_id: str, state: dict[str, Any], ttl: int) -> None:
        """Store the ceremony state under ``challenge_id``.

        Args:
            challenge_id (str): Handle returned to the client.
            state (dict[str, Any]): Opaque state from ``fido2``.
            ttl (int): Seconds after which the state is unusable.
        """
        ...

    async def pop(self, challenge_id: str) -> dict[str, Any] | None:
        """Remove and return the state stored under ``challenge_id``.

        Args:
            challenge_id (str): Handle handed back by the client.

        Returns:
            dict[str, Any] | None: The state, or ``None`` when it is
            unknown, already used, or expired.
        """
        ...


class MemoryWebAuthnChallengeStore:
    """In-process challenge store.

    Correct for a single worker. With more than one replica a ceremony
    that begins on replica A and completes on replica B finds no state
    and fails — use :class:`RedisWebAuthnChallengeStore` there.
    """

    def __init__(self) -> None:
        """Initialize an empty store."""
        self._states: dict[str, tuple[dict[str, Any], float]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def put(self, challenge_id: str, state: dict[str, Any], ttl: int) -> None:
        """Store the ceremony state, dropping anything already expired.

        Args:
            challenge_id (str): Handle returned to the client.
            state (dict[str, Any]): Opaque state from ``fido2``.
            ttl (int): Seconds after which the state is unusable.
        """
        now = time.monotonic()
        async with self._lock:
            for key, (_, expires_at) in list(self._states.items()):
                if expires_at <= now:
                    del self._states[key]
            self._states[challenge_id] = (state, now + ttl)

    async def pop(self, challenge_id: str) -> dict[str, Any] | None:
        """Remove and return the state, honoring its expiry.

        Args:
            challenge_id (str): Handle handed back by the client.

        Returns:
            dict[str, Any] | None: The state, or ``None``.
        """
        async with self._lock:
            entry = self._states.pop(challenge_id, None)
        if entry is None:
            return None
        state, expires_at = entry
        if expires_at <= time.monotonic():
            return None
        return state


@runtime_checkable
class RedisLike(Protocol):
    """Minimal async Redis surface used by the Redis challenge store.

    Matches the relevant subset of ``redis.asyncio.Redis``.
    """

    def set(self, name: str, value: str, /, ex: int | None = None) -> Awaitable[Any]:
        """Store ``value`` under ``name`` with an optional expiry.

        Declared as returning an ``Awaitable`` instead of as ``async def``:
        ``redis.asyncio.Redis`` returns ``Awaitable``, not ``Coroutine``,
        and a protocol member spelled ``async def`` demands the narrower
        one — rejecting the client this protocol names.

        Args:
            name (str): The Redis key.
            value (str): The payload to store.
            ex (int | None): Expiry in seconds, when set.

        Returns:
            Awaitable[Any]: Whatever the client returns; unused.
        """
        ...

    def getdel(self, name: str, /) -> Awaitable[Any]:
        """Return the value at ``name`` and delete it, atomically.

        Args:
            name (str): The Redis key.

        Returns:
            Awaitable[Any]: The stored payload, or ``None`` when absent.
        """
        ...


class RedisWebAuthnChallengeStore:
    """Challenge store shared across replicas.

    Uses ``GETDEL`` so the read and the delete are one operation: two
    concurrent completions of the same ceremony cannot both find the
    state, which is what makes the single-use property hold under
    concurrency.
    """

    def __init__(self, redis: RedisLike, *, namespace: str = "webauthn") -> None:
        """Initialize the store.

        Args:
            redis (RedisLike): Async Redis client (e.g.
                ``redis.asyncio.Redis``).
            namespace (str): Prefix for every Redis key.
        """
        self._redis: RedisLike = redis
        self._namespace: str = namespace

    def _key(self, challenge_id: str) -> str:
        """Return the namespaced Redis key.

        Args:
            challenge_id (str): The ceremony handle.

        Returns:
            str: The Redis key.
        """
        return f"{self._namespace}:{challenge_id}"

    async def put(self, challenge_id: str, state: dict[str, Any], ttl: int) -> None:
        """Store the ceremony state with a Redis expiry.

        Args:
            challenge_id (str): Handle returned to the client.
            state (dict[str, Any]): Opaque state from ``fido2``.
            ttl (int): Seconds after which the state is unusable.
        """
        import json

        await self._redis.set(self._key(challenge_id), json.dumps(state), ex=ttl)

    async def pop(self, challenge_id: str) -> dict[str, Any] | None:
        """Remove and return the state via ``GETDEL``.

        Args:
            challenge_id (str): Handle handed back by the client.

        Returns:
            dict[str, Any] | None: The state, or ``None``.
        """
        import json

        raw = await self._redis.getdel(self._key(challenge_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        decoded: dict[str, Any] = json.loads(raw)
        return decoded


class WebAuthnService:
    """Registration and passwordless login backed by ``fido2``.

    Both ceremonies are two requests: *begin* returns the options the
    browser passes to ``navigator.credentials`` plus a ``challenge_id``,
    and *complete* verifies what the authenticator produced. The
    ``challenge_id`` names the server-side state; it is popped on
    verification, so a response can be used once.

    Attributes:
        user_model (type[BaseUserModel]): The project's user model.
        credential_model (type[BaseWebAuthnCredentialModel]): The
            project's credential model.
        auth_settings (AuthSettings): Supplies the relying-party
            identity, the allowed origins and the challenge TTL.
        challenge_store (WebAuthnChallengeStore): Where ceremony state
            lives between the two requests.
    """

    def __init__(
        self,
        *,
        user_model: type[BaseUserModel],
        credential_model: type[BaseWebAuthnCredentialModel],
        auth_settings: AuthSettings,
        challenge_store: WebAuthnChallengeStore | None = None,
    ) -> None:
        """Initialize the service and build the relying party.

        Args:
            user_model (type[BaseUserModel]): Concrete user model.
            credential_model (type[BaseWebAuthnCredentialModel]):
                Concrete credential model.
            auth_settings (AuthSettings): Populates the relying-party
                identity and the origin policy.
            challenge_store (WebAuthnChallengeStore | None): Where
                ceremony state lives. ``None`` (default) builds a
                :class:`MemoryWebAuthnChallengeStore`, which is correct
                for a single worker only.

        Raises:
            ImportError: When the ``[webauthn]`` extra is not installed.
            ValueError: When ``AUTH_WEBAUTHN_RP_ID`` is empty — a
                relying party with no identity would accept assertions
                bound to nothing.
        """
        from fido2.server import Fido2Server
        from fido2.webauthn import PublicKeyCredentialRpEntity

        if not auth_settings.AUTH_WEBAUTHN_RP_ID:
            raise ValueError(
                "AUTH_WEBAUTHN_RP_ID must be set — it is the domain the "
                "credential is bound to, and the whole phishing resistance "
                "rests on it.",
            )
        self.user_model: type[BaseUserModel] = user_model
        self.credential_model: type[BaseWebAuthnCredentialModel] = credential_model
        self.auth_settings: AuthSettings = auth_settings
        self.challenge_store: WebAuthnChallengeStore = (
            challenge_store or MemoryWebAuthnChallengeStore()
        )
        self._server = Fido2Server(
            PublicKeyCredentialRpEntity(
                id=auth_settings.AUTH_WEBAUTHN_RP_ID,
                name=auth_settings.AUTH_WEBAUTHN_RP_NAME,
            ),
            verify_origin=self._build_origin_verifier(),
        )

    def _build_origin_verifier(self) -> Any:
        """Return the origin predicate, or ``None`` for the default.

        ``AUTH_WEBAUTHN_ALLOWED_ORIGINS`` exists because the ``fido2``
        default accepts ``https://<rp_id>`` and its subdomains, which is
        right in production and wrong during development, where the
        frontend runs on ``http://localhost:5173``. Listing origins is
        an explicit decision; silently relaxing the check would not be.

        Returns:
            Any: A ``(origin) -> bool`` predicate when origins are
            configured, else ``None`` so ``fido2`` applies its default.
        """
        allowed = tuple(self.auth_settings.AUTH_WEBAUTHN_ALLOWED_ORIGINS)
        if not allowed:
            return None

        def _verify(origin: str) -> bool:
            return origin in allowed

        return _verify

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    async def register_begin(
        self,
        session: AsyncSession,
        *,
        user: BaseUserModel,
    ) -> tuple[dict[str, Any], str]:
        """Start registering a new authenticator for ``user``.

        The credentials the account already holds are sent as
        ``excludeCredentials``, so registering the same key twice is
        refused by the authenticator instead of creating a duplicate row
        that can never be told apart.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user (BaseUserModel): The authenticated account.

        Returns:
            tuple[dict[str, Any], str]: The options to hand to
            ``navigator.credentials.create()`` and the ``challenge_id``
            the client must echo back.
        """
        from fido2.webauthn import (
            AttestedCredentialData,
            PublicKeyCredentialUserEntity,
            ResidentKeyRequirement,
            UserVerificationRequirement,
        )

        existing = await self._credentials_for(session, user_id=user.id)
        options, state = self._server.register_begin(
            PublicKeyCredentialUserEntity(
                id=user.id.bytes,
                name=user.email,
                display_name=getattr(user, "name", None) or user.email,
            ),
            credentials=[
                AttestedCredentialData(record.credential_data) for record in existing
            ],
            resident_key_requirement=ResidentKeyRequirement(
                self.auth_settings.AUTH_WEBAUTHN_RESIDENT_KEY,
            ),
            user_verification=UserVerificationRequirement(
                self.auth_settings.AUTH_WEBAUTHN_USER_VERIFICATION,
            ),
        )
        challenge_id = await self._store_state(state)
        return dict(options), challenge_id

    async def register_complete(
        self,
        session: AsyncSession,
        *,
        user: BaseUserModel,
        challenge_id: str,
        response: dict[str, Any],
        name: str | None = None,
    ) -> BaseWebAuthnCredentialModel:
        """Verify the attestation and persist the new credential.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user (BaseUserModel): The authenticated account.
            challenge_id (str): Handle returned by
                :meth:`register_begin`.
            response (dict[str, Any]): The browser's registration
                response, serialized as WebAuthn JSON.
            name (str | None): User-supplied label for the
                authenticator.

        Returns:
            BaseWebAuthnCredentialModel: The persisted credential.

        Raises:
            UnauthorizedException: When the challenge is unknown or the
                attestation does not verify.
            ValidationException: When the authenticator returned a
                credential this account already registered.
        """
        state = await self._pop_state(challenge_id)
        try:
            auth_data = self._server.register_complete(state, response)
        except Exception as exc:
            raise UnauthorizedException(
                message="invalid WebAuthn registration",
            ) from exc
        credential = auth_data.credential_data
        if credential is None:
            raise UnauthorizedException(message="invalid WebAuthn registration")
        duplicate = await session.execute(
            select(self.credential_model).where(
                self.credential_model.credential_id == bytes(credential.credential_id),
            ),
        )
        if duplicate.scalar_one_or_none() is not None:
            raise ValidationException(
                message="this authenticator is already registered",
            )
        record = self.credential_model(
            user_id=user.id,
            credential_id=bytes(credential.credential_id),
            credential_data=bytes(credential),
            sign_count=auth_data.counter,
            name=name,
            transports=_transports_of(response),
            aaguid=credential.aaguid.hex(),
            backed_up=bool(auth_data.flags & _BACKUP_STATE_FLAG),
        )
        session.add(record)
        await session.flush()
        await session.refresh(record)
        return record

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def authenticate_begin(
        self,
        session: AsyncSession,
        *,
        email: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Start a login ceremony.

        With no ``email`` the ceremony is **discoverable**: the options
        carry no credential list, and the authenticator picks the
        account itself — the passwordless flow. Passing an ``email``
        narrows ``allowCredentials`` to that account's keys, which helps
        an authenticator that stores no resident credential.

        An unknown ``email`` produces a ceremony with an empty allow
        list rather than an error: answering differently would turn the
        endpoint into a way to enumerate accounts.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            email (str | None): Account to narrow the ceremony to.

        Returns:
            tuple[dict[str, Any], str]: The options to hand to
            ``navigator.credentials.get()`` and the ``challenge_id``
            the client must echo back.
        """
        from fido2.webauthn import AttestedCredentialData, UserVerificationRequirement

        credentials: list[Any] = []
        if email:
            user = await self._user_by_email(session, email)
            if user is not None:
                records = await self._credentials_for(session, user_id=user.id)
                credentials = [
                    AttestedCredentialData(record.credential_data) for record in records
                ]
        options, state = self._server.authenticate_begin(
            credentials=credentials,
            user_verification=UserVerificationRequirement(
                self.auth_settings.AUTH_WEBAUTHN_USER_VERIFICATION,
            ),
        )
        challenge_id = await self._store_state(state)
        return dict(options), challenge_id

    async def authenticate_complete(
        self,
        session: AsyncSession,
        *,
        challenge_id: str,
        response: dict[str, Any],
    ) -> BaseUserModel:
        """Verify the assertion and return the authenticated user.

        The signature counter is checked here rather than by ``fido2``:
        a counter that did not advance since the last assertion is the
        spec's cloned-authenticator signal. Authenticators that always
        report ``0`` (most platform passkeys) are exempt, since for them
        a non-advancing counter carries no information.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            challenge_id (str): Handle returned by
                :meth:`authenticate_begin`.
            response (dict[str, Any]): The browser's authentication
                response, serialized as WebAuthn JSON.

        Returns:
            BaseUserModel: The authenticated user.

        Raises:
            UnauthorizedException: When the challenge is unknown, the
                credential is not registered, the assertion does not
                verify, the counter went backwards, or the account is
                inactive.
        """
        from fido2.webauthn import AttestedCredentialData, AuthenticationResponse

        state = await self._pop_state(challenge_id)
        try:
            parsed = AuthenticationResponse.from_dict(response)
        except Exception as exc:
            raise UnauthorizedException(message="invalid WebAuthn assertion") from exc
        record = await self._credential_by_id(session, bytes(parsed.raw_id))
        if record is None:
            raise UnauthorizedException(message="unknown credential")
        try:
            self._server.authenticate_complete(
                state,
                [AttestedCredentialData(record.credential_data)],
                parsed,
            )
        except Exception as exc:
            raise UnauthorizedException(message="invalid WebAuthn assertion") from exc
        counter = parsed.response.authenticator_data.counter
        if counter != 0 and counter <= record.sign_count:
            raise UnauthorizedException(
                message="authenticator signature counter did not advance",
            )
        user: BaseUserModel | None = await session.get(self.user_model, record.user_id)
        if user is None or not user.is_active:
            raise UnauthorizedException(message="account is not active")
        record.sign_count = counter
        record.last_used_at = utcnow()
        user.last_login_at = utcnow()
        await session.flush()
        await session.refresh(user)
        return user

    # ------------------------------------------------------------------
    # Credential management
    # ------------------------------------------------------------------

    async def list_credentials(
        self,
        session: AsyncSession,
        *,
        user: BaseUserModel,
    ) -> list[BaseWebAuthnCredentialModel]:
        """Return every credential registered by ``user``.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user (BaseUserModel): The authenticated account.

        Returns:
            list[BaseWebAuthnCredentialModel]: The credentials, empty
            when the account registered none.
        """
        return await self._credentials_for(session, user_id=user.id)

    async def delete_credential(
        self,
        session: AsyncSession,
        *,
        user: BaseUserModel,
        credential_id: bytes,
    ) -> None:
        """Remove one of ``user``'s credentials.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user (BaseUserModel): The authenticated account.
            credential_id (bytes): Raw credential ID to remove.

        Raises:
            NotFoundException: When the account holds no such
                credential. Scoped to the caller, so the endpoint never
                reveals whether the ID exists on another account.
        """
        result = await session.execute(
            select(self.credential_model).where(
                self.credential_model.user_id == user.id,
                self.credential_model.credential_id == credential_id,
            ),
        )
        record: BaseWebAuthnCredentialModel | None = result.scalar_one_or_none()
        if record is None:
            raise NotFoundException(message="credential not found")
        await session.delete(record)
        await session.flush()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _store_state(self, state: dict[str, Any]) -> str:
        """Persist ceremony state under a fresh handle.

        Args:
            state (dict[str, Any]): Opaque state from ``fido2``.

        Returns:
            str: The handle to return to the client.
        """
        challenge_id = secrets.token_urlsafe(CHALLENGE_ID_BYTES)
        await self.challenge_store.put(
            challenge_id,
            dict(state),
            self.auth_settings.AUTH_WEBAUTHN_CHALLENGE_TTL_SECONDS,
        )
        return challenge_id

    async def _pop_state(self, challenge_id: str) -> dict[str, Any]:
        """Consume the ceremony state for ``challenge_id``.

        Args:
            challenge_id (str): Handle handed back by the client.

        Returns:
            dict[str, Any]: The stored state.

        Raises:
            UnauthorizedException: When the handle is unknown, already
                used, or expired.
        """
        state = await self.challenge_store.pop(challenge_id)
        if state is None:
            raise UnauthorizedException(
                message="WebAuthn challenge is unknown or expired",
            )
        return state

    async def _credentials_for(
        self,
        session: AsyncSession,
        *,
        user_id: Any,
    ) -> list[BaseWebAuthnCredentialModel]:
        """Load every credential belonging to a user.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user_id (Any): Owner's primary key.

        Returns:
            list[BaseWebAuthnCredentialModel]: The credentials.
        """
        result = await session.execute(
            select(self.credential_model)
            .where(self.credential_model.user_id == user_id)
            .order_by(self.credential_model.created_at),
        )
        return list(result.scalars().all())

    async def _credential_by_id(
        self,
        session: AsyncSession,
        credential_id: bytes,
    ) -> BaseWebAuthnCredentialModel | None:
        """Find a credential by its raw ID.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            credential_id (bytes): Raw credential ID from the assertion.

        Returns:
            BaseWebAuthnCredentialModel | None: The credential, or
            ``None``.
        """
        result = await session.execute(
            select(self.credential_model).where(
                self.credential_model.credential_id == credential_id,
            ),
        )
        record: BaseWebAuthnCredentialModel | None = result.scalar_one_or_none()
        return record

    async def _user_by_email(
        self,
        session: AsyncSession,
        email: str,
    ) -> BaseUserModel | None:
        """Find a user by email.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            email (str): Address to look up, normalized to lowercase.

        Returns:
            BaseUserModel | None: The user, or ``None``.
        """
        result = await session.execute(
            select(self.user_model).where(
                self.user_model.email == email.strip().lower(),
            ),
        )
        user: BaseUserModel | None = result.scalar_one_or_none()
        return user


_BACKUP_STATE_FLAG: int = 0x10
"""``BS`` bit of the authenticator-data flags — credential is backed up.

Defined by the WebAuthn Level 3 authenticator data flags. Read at
registration to record whether the passkey is synced (survives losing
the device) or device-bound (does not), which is the difference between
account recovery being a convenience and being mandatory.
"""


def _transports_of(response: dict[str, Any]) -> str | None:
    """Extract the transport hints the browser reported.

    Args:
        response (dict[str, Any]): The registration response.

    Returns:
        str | None: Comma-separated transports, or ``None`` when the
        browser reported none (older browsers omit the field).
    """
    inner = response.get("response")
    if not isinstance(inner, dict):
        return None
    transports = inner.get("transports")
    if not isinstance(transports, list) or not transports:
        return None
    return ",".join(str(item) for item in transports)


__all__: list[str] = [
    "CHALLENGE_ID_BYTES",
    "MemoryWebAuthnChallengeStore",
    "RedisWebAuthnChallengeStore",
    "WebAuthnChallengeStore",
    "WebAuthnService",
]
