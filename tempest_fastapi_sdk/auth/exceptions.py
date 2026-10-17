"""Refusals of the bundled auth flow that carry their own ``code``."""

from dataclasses import dataclass

from tempest_fastapi_sdk.exceptions.conflict import ConflictException
from tempest_fastapi_sdk.exceptions.forbidden import ForbiddenException
from tempest_fastapi_sdk.exceptions.jwt import InvalidTokenException
from tempest_fastapi_sdk.exceptions.too_many_requests import (
    TooManyRequestsException,
)
from tempest_fastapi_sdk.exceptions.unauthorized import UnauthorizedException
from tempest_fastapi_sdk.exceptions.validation import ValidationException


class MFAAlreadyEnrolledException(ConflictException):
    """Raised when enrollment is requested for an account whose MFA is active.

    ``/mfa/enroll`` only requires a bearer access token. Enrolling again
    over an active second factor would wipe the recovery codes and reset
    ``totp_enabled_at``, which switches MFA off with nothing but that
    token, skipping the password and the TOTP code that ``/mfa/disable``
    demands. Rotating the secret therefore goes through ``/mfa/disable``
    first, then a fresh enroll and confirm.

    A pending enrollment (secret staged, never confirmed) is not active,
    so enrolling again over it is allowed and rotates the staged secret.
    """

    message: str = "MFA is already active — disable it before enrolling again"
    code: str = "MFA_ALREADY_ENROLLED"


class MFANotEnrolledException(ValidationException):
    """Raised when a flow that proves the second factor meets an account without one.

    :meth:`~tempest_fastapi_sdk.UserAuthService.change_password_with_mfa`
    rotates the password on the strength of a TOTP or recovery code; an
    account with no active factor has nothing to prove, so it is refused
    rather than silently downgraded to a password change with no proof.
    The ``AUTH_MFA_ENABLED`` kill-switch counts: with it off, every account
    reads as not enrolled, exactly as at login.
    """

    message: str = "MFA is not active for this account"
    code: str = "MFA_NOT_ENROLLED"


class AccountInactiveException(ForbiddenException):
    """Raised when the right password opens a deactivated account.

    Only under ``AUTH_REVEAL_INACTIVE_ACCOUNT``: by default a deactivated
    account answers the same ``401`` as a wrong password, so the response
    does not say which emails have an account. Revealing it to whoever
    proved the password enumerates nothing — that caller already owns
    the account — and spares them a password reset that would not let
    them in either.

    The social-login counterpart is
    :class:`~tempest_fastapi_sdk.OAuthAccountInactiveException`, which
    keeps its own ``OAUTH_ACCOUNT_INACTIVE`` code and ``401``.
    """

    message: str = "Account is not active"
    code: str = "ACCOUNT_INACTIVE"


class LoginThrottledException(TooManyRequestsException):
    """Raised when ``POST /auth/login`` is over its attempt budget.

    The same class for the per-email and the per-client budget, so the
    answer never says which of the two ran out — saying it would tell a
    caller that the email is being guessed at. The body is the same for an
    email that has an account and one that does not.
    """

    message: str = "too many login attempts, try again later"
    code: str = "LOGIN_THROTTLED"


class SignupThrottledException(TooManyRequestsException):
    """Raised when ``POST /auth/signup`` is over the per-client budget."""

    message: str = "too many signup attempts, try again later"
    code: str = "SIGNUP_THROTTLED"


class MfaThrottledException(TooManyRequestsException):
    """Raised when ``POST /auth/mfa/verify`` is over the per-account budget."""

    message: str = "too many invalid MFA codes, try again later"
    code: str = "MFA_THROTTLED"


@dataclass(frozen=True, slots=True)
class AuthExceptions:
    """Which exception class ``UserAuthService`` raises at each refusal.

    A service adopting the bundled flow keeps the ``code`` its clients
    already handle by passing its own classes here, instead of overriding
    methods only to swap the exception. Each field is typed with the
    default class, so a replacement must subclass it: the HTTP status
    (inherited) and the cost of the refusal (unchanged code path) stay
    what they were, and only the class — and with it the ``code`` —
    changes.

    Every raise still passes ``message`` / ``details`` / ``message_key``
    as keyword arguments, so a replacement keeps the
    :class:`~tempest_fastapi_sdk.AppException` constructor.

    Attributes:
        invalid_credentials (type[UnauthorizedException]): Wrong
            password, unknown email, and — unless
            ``AUTH_REVEAL_INACTIVE_ACCOUNT`` is on — a deactivated
            account, on ``login``.
        account_inactive (type[ForbiddenException]): The right password
            on a deactivated account, on ``login``, when
            ``AUTH_REVEAL_INACTIVE_ACCOUNT`` is on.
        email_taken (type[ConflictException]): Signup or email change to
            an address another account holds.
        password_too_short (type[ValidationException]): A password below
            the policy minimum.
        password_too_long (type[ValidationException]): A password above
            the policy's byte limit (bcrypt's 72).
        password_too_weak (type[ValidationException]): A password
            missing a character class under complexity mode.
        invalid_token (type[InvalidTokenException]): A single-use link
            (activation, password reset, email change or verification)
            that is unknown, used, expired, or points to a user that no
            longer exists. Refresh tokens are not covered.
    """

    invalid_credentials: type[UnauthorizedException] = UnauthorizedException
    account_inactive: type[ForbiddenException] = AccountInactiveException
    email_taken: type[ConflictException] = ConflictException
    password_too_short: type[ValidationException] = ValidationException
    password_too_long: type[ValidationException] = ValidationException
    password_too_weak: type[ValidationException] = ValidationException
    invalid_token: type[InvalidTokenException] = InvalidTokenException


__all__: list[str] = [
    "AccountInactiveException",
    "AuthExceptions",
    "LoginThrottledException",
    "MFAAlreadyEnrolledException",
    "MFANotEnrolledException",
    "MfaThrottledException",
    "SignupThrottledException",
]
