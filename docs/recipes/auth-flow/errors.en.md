# Your product's error codes

Use to replace the flow's error codes with your product's own via `AuthExceptions`.

Part of the [Bundled auth flow (signup / activate / login / reset)](../auth-flow.md) recipe.

An existing service has clients handling its own `code`s —
`ERROR_USER_INVALID_CREDENTIALS`, `USER_ALREADY_EXISTS`, `INVALID_PASSWORD`. The
SDK answers `UNAUTHORIZED`, `CONFLICT`, `VALIDATION_ERROR`, and the catalog
swaps the **message**, not the code. Without a seam, adopting
`UserAuthService` meant overriding methods only to swap the exception.

`AuthExceptions` is that seam: it says which class is raised at each refusal
point.

```python
# src/services/auth.py

from tempest_fastapi_sdk import AuthExceptions, UserAuthService
from tempest_fastapi_sdk.exceptions import (
    ConflictException,
    ForbiddenException,
    InvalidTokenException,
    UnauthorizedException,
    ValidationException,
)

from src.core.settings import settings
from src.db.models import UserModel, UserTokenModel


class InvalidCredentialsError(UnauthorizedException):
    """Wrong email or password."""

    code: str = "ERROR_USER_INVALID_CREDENTIALS"


class UserDeactivatedError(ForbiddenException):
    """Deactivated account, revealed to whoever proved the password."""

    code: str = "USER_ACCOUNT_DEACTIVATED"


class UserAlreadyExistsError(ConflictException):
    """Email already registered."""

    code: str = "USER_ALREADY_EXISTS"


class InvalidPasswordError(ValidationException):
    """Short or weak password."""

    code: str = "INVALID_PASSWORD"


class PasswordTooLongError(ValidationException):
    """Password above bcrypt's limit."""

    code: str = "PASSWORD_TOO_LONG"


class InvalidResetTokenError(InvalidTokenException):
    """A single-use link that does not work."""

    code: str = "INVALID_PASSWORD_RESET_TOKEN"


auth_service = UserAuthService(
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
    exceptions=AuthExceptions(
        invalid_credentials=InvalidCredentialsError,
        account_inactive=UserDeactivatedError,
        email_taken=UserAlreadyExistsError,
        password_too_short=InvalidPasswordError,
        password_too_long=PasswordTooLongError,
        password_too_weak=InvalidPasswordError,
        invalid_token=InvalidResetTokenError,
    ),
)
```

Measured with `register_exception_handlers(app, catalog=default_message_catalog())`
and `AUTH_REVEAL_INACTIVE_ACCOUNT=true`:

| Request | Without `exceptions=` | With the map above |
|---------|-----------------------|--------------------|
| login, wrong password | `401` `UNAUTHORIZED` | `401` `ERROR_USER_INVALID_CREDENTIALS` |
| login, deactivated account, right password | `403` `ACCOUNT_INACTIVE` | `403` `USER_ACCOUNT_DEACTIVATED` |
| signup, short password | `422` `VALIDATION_ERROR` | `422` `INVALID_PASSWORD` |
| signup, 73 bytes | `422` `VALIDATION_ERROR` | `422` `PASSWORD_TOO_LONG` |
| signup, repeated email | `409` `CONFLICT` | `409` `USER_ALREADY_EXISTS` |

Each field is typed with the default class, so yours **must** subclass it.
That is what holds the status: an `InvalidCredentialsError` is an
`UnauthorizedException`, so it stays `401`, and the refusal path — including
login's single bcrypt — is the same. The class changes, and the `code` with it.

!!! tip "The message for your `code`"
    For the password violations and the deactivated account the SDK passes
    its own `message_key` (`PASSWORD_TOO_SHORT`, `ACCOUNT_INACTIVE`, …), so
    `detail` stays translated with your class. Elsewhere the catalog looks up
    **your** `code`; without a translation for it, `detail` is the SDK's
    English message (`invalid email or password`). Add yours with
    `default_message_catalog().merge({"en-US": {"ERROR_USER_INVALID_CREDENTIALS": "..."}})`.

!!! note "What `invalid_token` covers"
    The single-use links: activation, password reset, email change and
    verification — unknown, used, expired, or pointing to a user that no
    longer exists. Since v0.305.0 a password reset whose user was removed is
    an `InvalidTokenException` like its siblings, no longer a `404`. Refresh
    tokens are left out: they are a different contract with the client.

**Recap.** `AuthExceptions` swaps the class — and the `code` — at each refusal
point, without overrides and without changing status or cost. Password
violations already carry their own `message_key`, and
`AUTH_REVEAL_INACTIVE_ACCOUNT` shows the `403` only to whoever proved the
password.
