# Firebase auth (ID token)

Your mobile app signs in with Firebase, gets an **ID token** and sends
that token to your API. The backend issued nothing — it only has to
**prove the token is genuine** before resolving who the caller is.

`FirebaseAuth` packages that whole path:

- it initializes the `firebase_admin` app **exactly once**, even when
  you build the authenticator in two different modules;
- it verifies the ID token off the event loop (Google's verifier is
  synchronous);
- it hands you a **typed identity** (`FirebaseIdentity`), not a
  `dict[str, Any]`; and
- it translates every Firebase failure into the SDK's exception
  hierarchy, with a distinct `code` for each one.

!!! info "Installation"
    Needs the `[firebase]` extra — `uv add "tempest-fastapi-sdk[firebase]"`.

    It is **heavy**: `firebase-admin` pulls `grpcio`, `protobuf`,
    `google-api-core`, `google-auth` and the Firestore/Storage clients.
    Measured with `firebase-admin` 7.5.0 in a clean venv: **33 packages,
    52 MB**. That is why it stays **out of the `[all]` extra**, and why
    the import is lazy — `import tempest_fastapi_sdk` keeps working
    without it installed.

!!! info "When to use this"
    Use it when **the client already arrives with a Firebase ID token**
    (a Flutter / React Native app, or web using the Firebase JS SDK).

    - If **your** service is the one logging users in and issuing
      tokens, you want [Auth flow](auth-flow.en.md).
    - If you receive an **opaque** bearer and validate it by asking an
      upstream `userinfo` endpoint, you want
      [Introspection auth](introspection-auth.en.md).

## The minimal path

Build it once, in the dependencies layer:

```python
# src/api/dependencies/auth.py
from tempest_fastapi_sdk import FirebaseAuth

from src.core.settings import settings

firebase = FirebaseAuth(
    credentials_path=settings.FIREBASE_CREDENTIALS_PATH,
    project_id=settings.FIREBASE_PROJECT_ID,
)
```

Then use the methods directly as dependencies:

```python
# src/api/routers/profile.py
from fastapi import APIRouter, Depends

from tempest_fastapi_sdk import FirebaseAuth, FirebaseIdentity

from src.core.settings import settings

firebase = FirebaseAuth(credentials_path=settings.FIREBASE_CREDENTIALS_PATH)

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("/me")
async def me(
    identity: FirebaseIdentity = Depends(firebase.get_identity),
) -> dict[str, str]:
    """Return the caller authenticated by the Firebase ID token."""
    return {"uid": identity.uid, "email": identity.email or ""}


@router.get("/uid")
async def uid(user_id: str = Depends(firebase.get_uid)) -> dict[str, str]:
    """When you only need the id, not the whole identity."""
    return {"uid": user_id}
```

That is it. A request without `Authorization` gets **401**; an expired,
tampered or wrong-project token gets **401** — each with its own `code`.

!!! tip "Register the exception handlers"
    `FirebaseAuth` raises subclasses of the SDK's own
    `UnauthorizedException` and `ForbiddenException`. Call
    `register_exception_handlers(app)` (the SDK's `create_app()` already
    does) so they become 401/403 with a
    `{"detail": ..., "code": ...}` body instead of a 500.

## How it works, piece by piece

### Idempotent initialization

`firebase_admin.initialize_app()` **raises `ValueError` on the second
call**. That is why every service ends up with the same
`get_app()` / `except ValueError` block copied across three files.

`FirebaseAuth` owns that block. Building it twice with the same
`app_name` reuses the existing app:

```python
from tempest_fastapi_sdk import FirebaseAuth

from src.core.settings import settings

first = FirebaseAuth(credentials_path=settings.FIREBASE_CREDENTIALS_PATH)
second = FirebaseAuth(credentials_path=settings.FIREBASE_CREDENTIALS_PATH)
```

This is not theory: `tests/auth/test_firebase.py` builds two
authenticators and asserts both point at the **same** app.

Need to talk to **two Firebase projects** in one process? Give them
different names:

```python
from tempest_fastapi_sdk import FirebaseAuth

from src.core.settings import settings

consumers = FirebaseAuth(
    credentials_path=settings.FIREBASE_CREDENTIALS_PATH,
    app_name="consumers",
)
drivers = FirebaseAuth(
    credentials_path=settings.FIREBASE_DRIVERS_CREDENTIALS_PATH,
    app_name="drivers",
)
```

### Where the credential comes from

Three channels, in this order of precedence:

1. `credentials_json` — the service-account JSON **inline**, for
   deployments that inject secrets as environment variables and mount
   no volume;
2. `credentials_path` — the service-account file on disk;
3. nothing — falls back to the environment's default credential
   (`GOOGLE_APPLICATION_CREDENTIALS`, or the metadata server when you
   run inside Google's infrastructure).

```python
import os

from tempest_fastapi_sdk import FirebaseAuth

firebase = FirebaseAuth(
    credentials_json=os.environ["FIREBASE_CREDENTIALS_JSON"],
    project_id="my-app-3f21c",
)
```

Invalid JSON, a missing file, or an environment with no default
credential all become `FirebaseCredentialError` — a `RuntimeError`,
**not** an `AppException`. The reason: this is a configuration failure,
it happens at construction, never in the middle of a request.

### The typed identity

`FirebaseIdentity` is a frozen dataclass. The handler never sees the raw
dict:

```python
import asyncio

from tempest_fastapi_sdk import FirebaseAuth, FirebaseIdentity

from src.core.settings import settings

firebase = FirebaseAuth(credentials_path=settings.FIREBASE_CREDENTIALS_PATH)


async def main() -> None:
    """Run this example."""
    identity: FirebaseIdentity = await firebase.verify("eyJhbGciOi...")
    print(identity.uid)
    print(identity.email, identity.email_verified)
    print(identity.phone_number)
    print(identity.provider)          # "google.com", "password", "phone", ...
    print(identity.claims["role"])    # custom claims stay reachable


asyncio.run(main())
```

`claims` keeps **everything** the token carried, including custom claims
you set with `set_custom_user_claims`. The named fields are what 99% of
routes use; `claims` is the escape hatch for the rest.

### The errors, one `code` per failure

| Situation | Exception | HTTP | `code` |
| --- | --- | --- | --- |
| No `Authorization` header | `FirebaseTokenMissingError` | 401 | `FIREBASE_TOKEN_MISSING` |
| Malformed token, bad signature, other project | `FirebaseTokenInvalidError` | 401 | `FIREBASE_TOKEN_INVALID` |
| Expired token | `FirebaseTokenExpiredError` | 401 | `FIREBASE_TOKEN_EXPIRED` |
| Revoked token (only with `check_revoked=True`) | `FirebaseTokenRevokedError` | 401 | `FIREBASE_TOKEN_REVOKED` |
| Disabled user (only with `check_revoked=True`) | `FirebaseUserDisabledError` | **403** | `FIREBASE_USER_DISABLED` |
| Google certificates unreachable | `FirebaseUnavailableError` | 401 | `FIREBASE_UNAVAILABLE` |

!!! note "Why the `except` ordering matters"
    On `firebase-admin` 7.5.0, `ExpiredIdTokenError` and
    `RevokedIdTokenError` are **subclasses** of `InvalidIdTokenError`
    (measured, not deduced). An implementation catching the parent first
    would collapse all three cases into `FIREBASE_TOKEN_INVALID` — and
    the client would lose the very information that decides between
    "refresh the token" and "sign in again". The SDK's parametrized test
    pins that ordering.

!!! warning "A disabled user is 403, not 401"
    They **proved** who they are; what is missing is permission. That is
    why it is the only failure the soft variant below still raises.

### The soft variant — a route serving anonymous and signed-in callers

`get_optional_identity` returns `None` instead of raising:

```python
from fastapi import APIRouter, Depends

from tempest_fastapi_sdk import FirebaseAuth, FirebaseIdentity

from src.core.settings import settings

firebase = FirebaseAuth(credentials_path=settings.FIREBASE_CREDENTIALS_PATH)

router = APIRouter(prefix="/api/feed", tags=["feed"])


@router.get("/")
async def feed(
    identity: FirebaseIdentity | None = Depends(firebase.get_optional_identity),
) -> dict[str, bool]:
    """Personalize when a token is present, serve anonymously otherwise."""
    return {"personalized": identity is not None}
```

No header, `None`. A token that fails verification, also `None` (logged
at `DEBUG` with the `code`, **never** with the token). A disabled user
still gets 403.

Need to narrow the type inside the handler? Use the SDK's guard:

```python
from fastapi import APIRouter, Depends

from tempest_fastapi_sdk import FirebaseAuth, FirebaseIdentity
from tempest_fastapi_sdk.auth import require_authenticated

from src.core.settings import settings

firebase = FirebaseAuth(credentials_path=settings.FIREBASE_CREDENTIALS_PATH)

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.post("/")
async def create_order(
    maybe: FirebaseIdentity | None = Depends(firebase.get_optional_identity),
) -> dict[str, str]:
    """Accept the request only when a token arrived and verified."""
    identity: FirebaseIdentity = require_authenticated(maybe)
    return {"uid": identity.uid}
```

### From the `uid` to **your** user

The SDK does not decide how a Firebase `uid` becomes a user in your
database — that is your rule (a column lookup, just-in-time
provisioning, a call to another service). `FirebaseUserResolver` is the
seam:

```python
from fastapi import APIRouter, Depends

from tempest_fastapi_sdk import (
    FirebaseAuth,
    FirebaseIdentity,
    FirebaseUserResolver,
)

from src.core.settings import settings
from src.db.models import UserModel
from src.db.repositories import UserRepository

firebase = FirebaseAuth(credentials_path=settings.FIREBASE_CREDENTIALS_PATH)
repository = UserRepository()


async def load_user(identity: FirebaseIdentity) -> UserModel | None:
    """Map the verified identity onto the local user."""
    return await repository.get_by_firebase_uid(identity.uid)


users: FirebaseUserResolver[UserModel] = FirebaseUserResolver(firebase, load_user)

router = APIRouter(prefix="/api/account", tags=["account"])


@router.get("/")
async def account(user: UserModel = Depends(users.get_user)) -> dict[str, str]:
    """The route already receives the database user, concretely typed."""
    return {"id": str(user.id)}
```

A resolver answering `None` means "this identity has no user here" — it
becomes a 401 with `FIREBASE_TOKEN_INVALID` and `details={"uid": ...}`,
not an empty response. `get_optional_user` is the soft version.

!!! note "`repository` and `UserModel` are your application's glue"
    `UserRepository.get_by_firebase_uid(...)` stands for your data
    layer — it is not part of the SDK. Swap it for your project's real
    call.

### `check_revoked` — the price of knowing right away

By default verification is **local**: signature plus claims against
Google's public certificates, which `firebase_admin` fetches and caches.
In that mode, revoking a session in the console does not kill the token
until it expires.

`check_revoked=True` makes every verification also ask the Firebase
backend whether the token was revoked and whether the user is disabled —
**one network round-trip per request**:

```python
from tempest_fastapi_sdk import FirebaseAuth

from src.core.settings import settings

firebase = FirebaseAuth(
    credentials_path=settings.FIREBASE_CREDENTIALS_PATH,
    check_revoked=True,
    clock_skew_seconds=5,
)
```

`clock_skew_seconds` gives slack to a client clock running fast — handy
when the app runs on a phone with a manually set time.

### Configuration through settings

The `FirebaseSettings` mixin already carries the three variables, with
title and description in the same shape as the others:

```python
from tempest_fastapi_sdk import BaseAppSettings, FirebaseAuth
from tempest_fastapi_sdk.settings import FirebaseSettings


class Settings(FirebaseSettings, BaseAppSettings):
    """Application settings."""


settings = Settings()
firebase = FirebaseAuth(**settings.firebase_kwargs())
```

| Variable | What for |
| --- | --- |
| `FIREBASE_PROJECT_ID` | The project the tokens belong to. Optional when the service account already carries it. |
| `FIREBASE_CREDENTIALS_PATH` | Path to the service-account file. |
| `FIREBASE_CREDENTIALS_JSON` | The same JSON inline, for deployments with no mounted volume. |

`firebase_kwargs()` **drops empty values**, so an unset variable leaves
the constructor default in place instead of passing an empty path.
`settings.enabled` tells whether an explicit service account is
configured — note that `False` does not prevent verification: the
environment's default credential still works.

## Testing

Patch `verify_id_token` on the real module — that way the error mapping
is exercised against the genuine exception classes, with their genuine
inheritance:

```python
from typing import Any

import pytest
from firebase_admin import auth as firebase_auth

from tempest_fastapi_sdk import FirebaseAuth, FirebaseTokenExpiredError

CLAIMS: dict[str, Any] = {"uid": "uid-123", "email": "person@example.com"}


async def test_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """An expired token becomes the SDK code, not Google's exception."""

    def fake_verify(id_token: str, **_: Any) -> dict[str, Any]:
        """Stand in for Google's verifier."""
        raise firebase_auth.ExpiredIdTokenError("expired", None)

    monkeypatch.setattr(firebase_auth, "verify_id_token", fake_verify)
    firebase = FirebaseAuth(credentials_path="tests/fixtures/service-account.json")

    with pytest.raises(FirebaseTokenExpiredError) as error:
        await firebase.verify("token")

    assert error.value.code == "FIREBASE_TOKEN_EXPIRED"
```

!!! tip "No network, no real credential"
    The SDK's suite generates a local RSA key and assembles a
    syntactically valid service-account file — `initialize_app` accepts
    it, because it never talks to Google. And a token that is not a JWT
    is rejected **without touching the network** (measured): the
    verifier checks the structure before fetching any certificate.

## Recap

- `FirebaseAuth` verifies **Firebase ID tokens**; Firebase issues them,
  your service does not.
- Initialization is **idempotent** per `app_name` — building twice
  reuses the same app; distinct names talk to distinct projects.
- The credential comes from inline JSON, a file, or the environment's
  default credential, in that order. Configuration failures raise
  `FirebaseCredentialError`.
- `get_identity` / `get_uid` are strict; `get_optional_identity` is the
  soft variant returning `None`. A disabled user is 403 in both.
- Every failure has its own `code`; the `except` ordering preserves the
  difference between expired, revoked and invalid.
- `FirebaseUserResolver[UserT]` links the identity to your database user
  without the SDK deciding the rule.
- The `[firebase]` extra is heavy (33 packages, 52 MB measured) and
  stays out of `[all]`; the import is lazy, so only instantiating needs
  it.
