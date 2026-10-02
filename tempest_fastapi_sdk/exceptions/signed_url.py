"""Signed-URL exceptions raised by :func:`~tempest_fastapi_sdk.utils.verify_path`.

Both are ``403``, not ``401``. A signed URL is a capability, not a login: no
credential the client could send would fix it, so there is nothing to
authenticate against (S3 answers a bad or expired presigned URL with ``403``
for the same reason). A ``401`` would also trip the "session expired, go to
login" interceptor most frontends install, although the user's session is
fine. The two codes stay distinct so the client can tell "ask the backend for
a fresh URL" (expired) from "this link was tampered with" (invalid).
"""

from tempest_fastapi_sdk.exceptions.forbidden import ForbiddenException


class InvalidSignedURLException(ForbiddenException):
    """Raised when a signed URL is missing its parameters or fails the MAC.

    Covers every way the signature can be wrong: absent or malformed
    ``expires``/``signature``, a tampered path or ``expires``, a different
    ``purpose`` or a different secret. They are deliberately not told apart,
    so the response leaks nothing about which part was rejected.
    """

    message: str = "Invalid signed URL"
    code: str = "SIGNED_URL_INVALID"


class ExpiredSignedURLException(ForbiddenException):
    """Raised when an authentic signed URL is past its ``expires`` instant.

    Only raised after the signature verified, so it never fires for a forged
    ``expires`` — that one is :class:`InvalidSignedURLException`.
    """

    message: str = "Signed URL has expired"
    code: str = "SIGNED_URL_EXPIRED"


__all__: list[str] = [
    "ExpiredSignedURLException",
    "InvalidSignedURLException",
]
