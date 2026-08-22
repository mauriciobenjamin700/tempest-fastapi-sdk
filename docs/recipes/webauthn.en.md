# WebAuthn / passkeys

TOTP proves the user holds a shared secret. A phishing page that asks for the
code and forwards it in real time defeats that — the server receives a valid
code and has no way to tell where it came from.

WebAuthn fixes this by construction: the signature is **bound to the origin**
that requested it. A credential registered for `app.example.com` produces
nothing a page on `app-example.com` can use, because the browser refuses to use
it there. That property — not "passwordless" — is what justifies the feature.

!!! info "Required extra"
    ```bash
    uv add "tempest-fastapi-sdk[auth,webauthn]"
    ```
    The extra brings in `fido2` (Yubico), which handles CBOR parsing, COSE
    keys, attestation formats and signature verification. The module imports
    without it; a project that never builds a `WebAuthnService` pays nothing.

## What you are building

Two ceremonies, two requests each:

```text
Register    POST /auth/webauthn/register/begin      (bearer) → options + challenge_id
            navigator.credentials.create(options.publicKey)
            POST /auth/webauthn/register/complete   (bearer) → credential stored

Login       POST /auth/webauthn/authenticate/begin            → options + challenge_id
            navigator.credentials.get(options.publicKey)
            POST /auth/webauthn/authenticate/complete         → access + refresh token
```

Plus two management routes: `GET /auth/webauthn/credentials` lists what the
account registered and `POST /auth/webauthn/credentials/delete` removes one.

## Step 1 — the credential table

A credential is a **public** key. Unlike a password hash, a full leak of this
table authenticates nobody.

```python
# src/db/models.py

from tempest_fastapi_sdk import (
    BaseUserModel,
    make_user_token_model,
    make_web_authn_credential_model,
)


class UserModel(BaseUserModel):
    """The project's user table."""

    __tablename__ = "users"


UserTokenModel = make_user_token_model(user_table="users")
UserWebAuthnCredentialModel = make_web_authn_credential_model(user_table="users")
```

Generate the migration as usual — `tempest db revision -m "webauthn credentials"`.

## Step 2 — the settings

```python
# src/core/settings.py

from tempest_fastapi_sdk import BaseAppSettings
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings


class Settings(AuthSettings, JWTSettings, BaseAppSettings):
    """Application settings."""


settings = Settings(
    JWT_SECRET="change-me-32-chars-minimum-secret",
    AUTH_WEBAUTHN_ENABLED=True,
    AUTH_WEBAUTHN_RP_ID="example.com",
    AUTH_WEBAUTHN_RP_NAME="Acme",
)
```

!!! danger "`AUTH_WEBAUTHN_RP_ID` is the security boundary"
    It is the domain the credential is bound to. It must be the site's origin
    domain or a registrable suffix of it: `example.com` covers
    `app.example.com`; the reverse is invalid and the browser refuses the
    ceremony. In development, use `localhost`.

    Changing the `rp_id` later **invalidates every registered credential** —
    they are bound to the old value. Pick the broadest domain you will want,
    not the most specific one.

!!! warning "Development origins"
    By default `fido2` accepts `https://<rp_id>` and its subdomains. A Vite
    frontend on `http://localhost:5173` does not match that rule, so the
    ceremony fails. List the origins explicitly:

    ```python
    settings = Settings(
        JWT_SECRET="change-me-32-chars-minimum-secret",
        AUTH_WEBAUTHN_ENABLED=True,
        AUTH_WEBAUTHN_RP_ID="localhost",
        AUTH_WEBAUTHN_ALLOWED_ORIGINS=["http://localhost:5173"],
    )
    ```

    When the list is populated it is the **whole** allowlist — the default rule
    no longer applies. Every entry is a page allowed to spend the credential,
    so keep it exact and never let a production value sit next to a development
    one in the same file.

## Step 3 — mount the router

```python
# src/api/app.py

from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    UserAuthService,
    WebAuthnService,
    make_auth_router,
)

from src.core.settings import settings
from src.db.models import UserModel, UserTokenModel, UserWebAuthnCredentialModel


def create_app() -> FastAPI:
    db = AsyncDatabaseManager(db_url=settings.DATABASE_URL)
    service = UserAuthService(
        user_model=UserModel,
        token_model=UserTokenModel,
        auth_settings=settings,
        jwt_settings=settings,
    )
    webauthn = WebAuthnService(
        user_model=UserModel,
        credential_model=UserWebAuthnCredentialModel,
        auth_settings=settings,
    )
    app = FastAPI()
    app.include_router(
        make_auth_router(
            service,
            session_factory=db.session_dependency,
            webauthn=webauthn,
        ),
    )
    return app
```

`AUTH_WEBAUTHN_ENABLED=True` without passing `webauthn=` raises `RuntimeError`
inside `create_app`. The application does not start with endpoints that would
answer 500 — the same rule `recovery_code_model` follows for MFA.

## Step 4 — the frontend

The browser speaks `ArrayBuffer`; the API speaks base64url. Let the browser do
the conversion with `PublicKeyCredential.parseCreationOptionsFromJSON` and
`.toJSON()` — without them you reimplement the conversion in both directions.

```javascript
// registration (the user is already signed in)
const begin = await fetch("/auth/webauthn/register/begin", {
  method: "POST",
  headers: { Authorization: `Bearer ${accessToken}` },
}).then((r) => r.json());

const credential = await navigator.credentials.create(
  PublicKeyCredential.parseCreationOptionsFromJSON(begin.options.publicKey),
);

await fetch("/auth/webauthn/register/complete", {
  method: "POST",
  headers: {
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    challenge_id: begin.challenge_id,
    credential: credential.toJSON(),
    name: "MacBook",
  }),
});
```

```javascript
// login with no password and no typed email
const begin = await fetch("/auth/webauthn/authenticate/begin", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({}),
}).then((r) => r.json());

const assertion = await navigator.credentials.get(
  PublicKeyCredential.parseRequestOptionsFromJSON(begin.options.publicKey),
);

const tokens = await fetch("/auth/webauthn/authenticate/complete", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    challenge_id: begin.challenge_id,
    credential: assertion.toJSON(),
  }),
}).then((r) => r.json());
```

!!! tip "With and without an email"
    Omitting `email` in `begin` is the **discoverable** flow: the options carry
    no credential list and the authenticator offers the accounts it stores.
    Passing `email` narrows the ceremony to one account — useful for a security
    key that stores no resident credential.

    An address nobody registered returns a normal ceremony with an empty list,
    not an error. Answering differently would turn the endpoint into an
    account-enumeration oracle.

## What the SDK verifies for you

| Check | Where | Why |
| --- | --- | --- |
| Signature + origin + `rp_id` | `fido2` | The core of the phishing resistance. |
| Challenge used exactly once | `WebAuthnChallengeStore.pop` | A captured response cannot be replayed. |
| Signature counter advanced | `authenticate_complete` | The spec's cloned-authenticator signal. Authenticators that always report `0` (most platform passkeys) are exempt — for them the counter carries no information. |
| Credential already registered | `register_complete` | Uniqueness is per table, not per account: the same key on two accounts would be two rows nobody can tell apart. |
| Account is active | `authenticate_complete` | Deactivating a user must close the passkey path too. |
| Delete scoped to the owner | `delete_credential` | Another account's ID answers 404 exactly like one that does not exist. |

## Multi-worker: the challenge store

The state between the two halves of a ceremony lives on the server — the client
must not be able to alter it, so a cookie will not do. The default is
in-process, correct for a single worker. With more than one replica, a ceremony
that starts on A and finishes on B finds no state at all:

```python
# src/api/app.py

from fastapi import FastAPI
from redis.asyncio import Redis

from tempest_fastapi_sdk import RedisWebAuthnChallengeStore, WebAuthnService

from src.core.settings import settings
from src.db.models import UserModel, UserWebAuthnCredentialModel


def build_webauthn() -> WebAuthnService:
    redis: Redis = Redis.from_url(settings.REDIS_URL)
    return WebAuthnService(
        user_model=UserModel,
        credential_model=UserWebAuthnCredentialModel,
        auth_settings=settings,
        challenge_store=RedisWebAuthnChallengeStore(redis),
    )
```

The Redis store uses `GETDEL`: read and delete become one operation, so two
concurrent completions of the same ceremony cannot both find the state.

## Account recovery

`backed_up` in the listing tells whether the credential is **synced** (an
iCloud / Google Password Manager passkey, which survives losing the device) or
**device-bound** (a physical security key, which does not).

The difference decides your product, not the SDK:

- Synced passkeys only? Recovery already exists: the user signs into the
  provider account on another device.
- Physical key? Register **two** and store one away, or keep a second factor
  (password + MFA) as the recovery path.

The delete endpoint does not stop you from removing the last credential.
Whether a password is still an acceptable fallback is the application's
decision, and the SDK cannot know it.

## Relationship to MFA

`POST /auth/webauthn/authenticate/complete` does **not** go through the MFA
challenge. That is deliberate: a passkey with user verification
(`AUTH_WEBAUTHN_USER_VERIFICATION="required"`) already proves possession of the
authenticator *and* a local factor (PIN, biometric) — exactly what the second
step exists to prove. Demanding TOTP on top would make the strongest login the
most annoying one.

The two coexist: the same account can hold a password + TOTP and passkeys, and
uses whichever is at hand.

## Recap

- `make_web_authn_credential_model(user_table=...)` creates the table; it stores
  public keys only.
- `WebAuthnService` runs both ceremonies; `make_auth_router(webauthn=...)`
  mounts the six routes when `AUTH_WEBAUTHN_ENABLED` is on.
- `AUTH_WEBAUTHN_RP_ID` is the security boundary, and changing it invalidates
  everything already registered.
- `AUTH_WEBAUTHN_ALLOWED_ORIGINS` is for development, and replaces the default
  rule entirely.
- The challenge is single-use; a counter that fails to advance is refused;
  deletion is scoped to the owner.
- Multi-replica requires `RedisWebAuthnChallengeStore`.

Next: [MFA (TOTP / 2FA)](mfa.md) for the classic second factor, or
[Refresh tokens](refresh-tokens.md) to revoke the session a passkey opened.
