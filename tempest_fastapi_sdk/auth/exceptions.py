"""Refusals of the bundled auth flow that carry their own ``code``."""

from tempest_fastapi_sdk.exceptions.conflict import ConflictException


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


__all__: list[str] = [
    "MFAAlreadyEnrolledException",
]
