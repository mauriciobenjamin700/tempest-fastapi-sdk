"""Sign and verify short-lived URLs that point at the app's own routes.

:meth:`AsyncMinIOClient.presigned_get_url
<tempest_fastapi_sdk.storage.AsyncMinIOClient.presigned_get_url>` signs a URL
for the **bucket**. When the frontend may only talk to the backend, a private
file needs a URL **of the backend** that the browser can open on its own —
``<img src>``, ``<video src>`` and a plain download link send no
``Authorization`` header, and a session cookie across the app's and the API's
domains runs into ``SameSite``. The answer is the same shape as a presigned
URL, aimed at the app: the mapper that already authorized the caller returns
``/files/<key>?expires=<ts>&signature=<mac>``, and the route checks the
signature before streaming.

Construction (standard library only — no extra):

* **Domain separation.** The MAC key is
  ``HMAC-SHA256(secret, "tempest-fastapi-sdk.signed-url.v1" NUL purpose)``,
  never the raw secret. Reusing ``JWT_SECRET`` is therefore safe, and a URL
  signed for ``purpose="files"`` does not verify for ``purpose="email-link"``.
* **What the MAC covers.** ``HMAC-SHA256(key, expires LF path)`` — the path
  **and** the expiry. Without ``expires`` inside the MAC a client could extend
  the validity at will. The query string beyond the two signed parameters is
  not covered.
* **Encoding.** The signature is URL-safe base64 without padding (43
  characters), so it needs no escaping in a query string.
* **Comparison.** :func:`hmac.compare_digest`, in constant time.

Path normalization rule: the signed path is the **decoded** path — the same
string the route sees in ``request.scope["path"]`` (and that ``{key:path}``
receives). :func:`sign_path` percent-encodes it for the URL it returns, and
Starlette decodes the request path before routing, so both sides MAC the same
string. ``/files/a%2Fb`` and ``/files/a/b`` therefore reach the same route
with the same key and share one signature; a key with a literal ``%`` is
signed as ``%`` and travels as ``%25``. Pass :func:`sign_path` the path as the
route will see it, never an already-encoded one.
"""

import base64
import hashlib
import hmac
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

from tempest_fastapi_sdk.exceptions.signed_url import (
    ExpiredSignedURLException,
    InvalidSignedURLException,
)
from tempest_fastapi_sdk.utils.datetime import to_utc, utcnow

SIGNED_URL_EXPIRES_PARAM: str = "expires"
"""Query parameter carrying the expiry, in unix seconds."""

SIGNED_URL_SIGNATURE_PARAM: str = "signature"
"""Query parameter carrying the URL-safe base64 HMAC-SHA256."""

_KEY_DERIVATION_LABEL: bytes = b"tempest-fastapi-sdk.signed-url.v1\x00"
"""Prefix of the purpose-bound key derivation.

Versioned so a future change of construction cannot verify URLs signed by
this one, and NUL-terminated so no ``purpose`` can extend the label.
"""


def _require_material(secret: str, purpose: str) -> None:
    """Reject empty key material before anything is signed or checked.

    An empty secret would make every signature forgeable, and an empty
    purpose would defeat domain separation, so both are programming errors
    rather than request failures.

    Args:
        secret (str): The application secret.
        purpose (str): The domain-separation label.

    Raises:
        ValueError: If ``secret`` or ``purpose`` is empty.
    """
    if not secret:
        raise ValueError("secret must be a non-empty string")
    if not purpose:
        raise ValueError("purpose must be a non-empty string")


def _signature(path: str, *, expires: int, secret: str, purpose: str) -> str:
    """Compute the signature of ``path`` valid until ``expires``.

    Args:
        path (str): The decoded route path.
        expires (int): Expiry instant, unix seconds.
        secret (str): The application secret.
        purpose (str): The domain-separation label.

    Returns:
        str: URL-safe base64 of the HMAC-SHA256, without padding.
    """
    key = hmac.new(
        secret.encode("utf-8"),
        _KEY_DERIVATION_LABEL + purpose.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    message = f"{expires}\n{path}".encode()
    digest = hmac.new(key, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def sign_path(
    path: str,
    *,
    secret: str,
    expires_in: timedelta,
    purpose: str,
    now: datetime | None = None,
) -> str:
    """Return ``path`` signed for ``purpose`` and valid for ``expires_in``.

    Call it where the caller has already been authorized — typically in the
    mapper that turns a stored key into the response schema — so only an
    allowed caller ever holds the URL.

    Args:
        path (str): The route path, **decoded**, exactly as the route will see
            it in ``request.scope["path"]`` (for example
            ``"/api/files/report 2026.pdf"``). Must start with ``/``.
        secret (str): The application secret. Domain separation makes reusing
            ``JWT_SECRET`` safe.
        expires_in (timedelta): How long the URL stays valid. Must be
            positive.
        purpose (str): Label binding the signature to one use (``"files"``,
            ``"email-link"``); a URL signed for one purpose never verifies for
            another.
        now (datetime | None): The signing instant. ``None`` uses the current
            UTC time; tests pass a fixed value. A naive datetime is read as
            UTC.

    Returns:
        str: The percent-encoded path followed by
        ``?expires=<unix seconds>&signature=<mac>``.

    Raises:
        ValueError: If ``path`` does not start with ``/``, ``expires_in`` is
            not positive, or ``secret``/``purpose`` is empty.
    """
    _require_material(secret, purpose)
    if not path.startswith("/"):
        raise ValueError(f"path must start with '/', got {path!r}")
    if expires_in <= timedelta(0):
        raise ValueError("expires_in must be positive")
    issued_at = to_utc(now) if now is not None else utcnow()
    expires = int((issued_at + expires_in).timestamp())
    query = urlencode(
        {
            SIGNED_URL_EXPIRES_PARAM: expires,
            SIGNED_URL_SIGNATURE_PARAM: _signature(
                path, expires=expires, secret=secret, purpose=purpose
            ),
        }
    )
    encoded_path = quote(path, safe="/")
    return f"{encoded_path}?{query}"


def verify_path(
    path: str,
    *,
    expires: int,
    signature: str,
    secret: str,
    purpose: str,
    now: datetime | None = None,
) -> None:
    """Check that ``signature`` authorizes ``path`` until ``expires``.

    The signature is checked first and the expiry second, so a forged
    ``expires`` is reported as invalid, not as expired. A URL stops being
    valid at the ``expires`` second itself.

    Args:
        path (str): The decoded request path — ``request.scope["path"]``.
        expires (int): The ``expires`` query parameter, unix seconds.
        signature (str): The ``signature`` query parameter.
        secret (str): The secret the URL was signed with.
        purpose (str): The purpose the URL was signed for.
        now (datetime | None): The verification instant. ``None`` uses the
            current UTC time; tests pass a fixed value. A naive datetime is
            read as UTC.

    Raises:
        InvalidSignedURLException: ``403`` when the signature does not match
            the path, ``expires``, ``purpose`` and secret.
        ExpiredSignedURLException: ``403`` when the signature is authentic but
            ``expires`` is not in the future.
        ValueError: If ``secret`` or ``purpose`` is empty.
    """
    _require_material(secret, purpose)
    expected = _signature(path, expires=expires, secret=secret, purpose=purpose)
    if not hmac.compare_digest(expected.encode("ascii"), signature.encode("utf-8")):
        raise InvalidSignedURLException()
    checked_at = to_utc(now) if now is not None else utcnow()
    if checked_at.timestamp() >= expires:
        raise ExpiredSignedURLException()


__all__: list[str] = [
    "SIGNED_URL_EXPIRES_PARAM",
    "SIGNED_URL_SIGNATURE_PARAM",
    "sign_path",
    "verify_path",
]
