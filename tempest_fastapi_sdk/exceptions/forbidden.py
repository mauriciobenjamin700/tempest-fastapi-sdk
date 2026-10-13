"""403 Forbidden exception."""

from tempest_fastapi_sdk.exceptions.base import AppException


class ForbiddenException(AppException):
    """Raised when the caller is authenticated but lacks permission."""

    status_code: int = 403
    message: str = "Forbidden"
    code: str = "FORBIDDEN"


class MFARequiredException(ForbiddenException):
    """Raised when a route needs a second factor the session never proved.

    The token is valid and the user is known, so the answer is ``403`` and
    not ``401``: logging in again with the password alone would not help.
    The client completes the two-step login (``/auth/mfa/verify``) and
    retries with the token that comes out of it.
    """

    message: str = "This operation requires a session that passed MFA"
    code: str = "MFA_REQUIRED"


__all__: list[str] = [
    "ForbiddenException",
    "MFARequiredException",
]
